"""Digital signatures with a certificate (PAdES, the European standard): sign a PDF with a
.p12 / .pfx certificate, optionally with a trusted timestamp, and check the signatures a
document already has. A personal certificate can be made here too: it proves the file was
not changed after signing, but has no legal value as a qualified signature.

The work is done by pyHanko; signing writes the signature as an incremental update, so the
signatures already in the file stay valid."""
import datetime
import io
import os

from .storage import path as data_path


def cert_dir():
    d = data_path("certificati")
    os.makedirs(d, exist_ok=True)
    return d


def _pw(password):
    return password.encode("utf-8") if password else None


def list_certificates():
    """The certificates made in Calamo or copied into its folder."""
    out = []
    for name in sorted(os.listdir(cert_dir())):
        if name.lower().endswith((".p12", ".pfx")):
            out.append({"name": os.path.splitext(name)[0], "path": os.path.join(cert_dir(), name)})
    return out


def create_certificate(name, email="", org="", password="", years=3):
    """A personal (self-signed) certificate in a .p12 protected by password."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    name = (name or "").strip()
    if not name:
        raise RuntimeError("Scrivi il tuo nome e cognome.")
    if len(password or "") < 4:
        raise RuntimeError("Scegli una password di almeno 4 caratteri per proteggere il certificato.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, name), x509.NameAttribute(NameOID.COUNTRY_NAME, "IT")]
    if org:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATION_NAME, org.strip()))
    if email:
        attrs.append(x509.NameAttribute(NameOID.EMAIL_ADDRESS, email.strip()))
    subject = x509.Name(attrs)
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(now - datetime.timedelta(minutes=5))
               .not_valid_after(now + datetime.timedelta(days=365 * int(years)))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False))
    if email:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.RFC822Name(email.strip())]), critical=False)
    cert = builder.sign(key, hashes.SHA256())
    data = pkcs12.serialize_key_and_certificates(name.encode("utf-8"), key, cert, None,
                                                 serialization.BestAvailableEncryption(password.encode("utf-8")))
    safe = "".join(c for c in name if c.isalnum() or c in " -_").strip() or "certificato"
    path = os.path.join(cert_dir(), f"{safe}.p12")
    k = 2
    while os.path.exists(path):
        path = os.path.join(cert_dir(), f"{safe} ({k}).p12")
        k += 1
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path, "name": os.path.splitext(os.path.basename(path))[0]}


def _load(path, password):
    from cryptography.hazmat.primitives.serialization import pkcs12
    with open(path, "rb") as f:
        data = f.read()
    try:
        key, cert, extra = pkcs12.load_key_and_certificates(data, _pw(password))
    except ValueError:
        raise RuntimeError("Password del certificato errata.")
    if key is None or cert is None:
        raise RuntimeError("Il file non contiene un certificato con la sua chiave privata.")
    return key, cert, extra or []


def _cn(name):
    from cryptography.x509.oid import NameOID
    v = name.get_attributes_for_oid(NameOID.COMMON_NAME)
    return v[0].value if v else name.rfc4514_string()


def certificate_info(path, password):
    _, cert, _ = _load(path, password)
    after = cert.not_valid_after_utc
    return {"name": _cn(cert.subject), "issuer": _cn(cert.issuer), "selfSigned": cert.subject == cert.issuer,
            "validTo": after.strftime("%d/%m/%Y"), "expired": after < datetime.datetime.now(datetime.timezone.utc)}


def sign(data, out_path, cert_path, password, page=0, box=None, field=None, reason="", location="",
         tsa_url="", show_date=True):
    """Sign the PDF bytes `data` into out_path. box: (x0, y0, x1, y1) in PDF coordinates of
    a new visible signature on `page`; field: the name of an empty signature field to use."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko.stamp import TextStampStyle

    _, cert, _ = _load(cert_path, password)               # a clear error for a wrong password
    signer = signers.SimpleSigner.load_pkcs12(pfx_file=cert_path, passphrase=_pw(password))
    if signer is None:
        raise RuntimeError("Non riesco a usare questo certificato.")
    w = IncrementalPdfFileWriter(io.BytesIO(data), strict=False)
    if not field:
        existing = {f[0] for f in fields.enumerate_sig_fields(w.prev, filled_status=None)}
        k = 1
        while f"Firma{k}" in existing:
            k += 1
        field = f"Firma{k}"
        fields.append_signature_field(w, fields.SigFieldSpec(sig_field_name=field, on_page=int(page),
                                                             box=tuple(int(round(v)) for v in box)))
    meta = signers.PdfSignatureMetadata(field_name=field, reason=reason or None, location=location or None,
                                        subfilter=fields.SigSeedSubFilter.PADES, md_algorithm="sha256",
                                        name=_cn(cert.subject))
    text = "Firmato digitalmente da\n%(signer)s" + ("\nData: %(ts)s" if show_date else "")
    style = TextStampStyle(stamp_text=text, timestamp_format="%d/%m/%Y %H:%M", border_width=1)
    timestamper = None
    if tsa_url:
        from pyhanko.sign.timestamps import HTTPTimeStamper
        timestamper = HTTPTimeStamper(tsa_url.strip(), timeout=15)
    pdf_signer = signers.PdfSigner(meta, signer=signer, stamp_style=style, timestamper=timestamper)
    buf = io.BytesIO()
    pdf_signer.sign_pdf(w, output=buf)
    tmp = out_path + ".calamo-tmp"
    with open(tmp, "wb") as f:
        f.write(buf.getvalue())
    os.replace(tmp, out_path)
    return {"path": out_path, "field": field, "signer": _cn(cert.subject)}


def verify(data):
    """Every signature of the PDF bytes: who signed, when, and whether the file is intact."""
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    import logging
    logging.getLogger("pyhanko").setLevel(logging.CRITICAL)     # a personal certificate is not an error
    logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)
    r = PdfFileReader(io.BytesIO(data), strict=False)
    out = []
    for es in r.embedded_signatures:
        item = {"field": es.field_name, "signer": "", "time": "", "intact": False, "valid": False,
                "trusted": False, "whole": False, "changedAfter": False, "summary": "", "selfSigned": False}
        try:
            cert = es.signer_cert
            nat = cert.subject.native
            item["signer"] = nat.get("common_name") or cert.subject.human_friendly
            item["selfSigned"] = cert.self_signed in ("yes", "maybe")
        except Exception:
            pass
        try:
            st = validate_pdf_signature(es, ValidationContext(allow_fetching=False))
            item["intact"], item["valid"], item["trusted"] = bool(st.intact), bool(st.valid), bool(st.trusted)
            t = st.timestamp_validity.timestamp if st.timestamp_validity else st.signer_reported_dt
            if t:
                item["time"] = t.astimezone().strftime("%d/%m/%Y %H:%M")
                item["timestamped"] = bool(st.timestamp_validity)
            cov = str(getattr(st, "coverage", ""))
            item["whole"] = "ENTIRE_FILE" in cov
            item["changedAfter"] = not item["whole"]
            mod = str(getattr(st, "modification_level", ""))
            if item["changedAfter"] and any(m in mod for m in ("LTA_UPDATES", "NONE", "FORM_FILLING")):
                item["changedAfter"] = False           # only signatures and filled fields came after it
                item["later"] = True
        except Exception as e:
            item["summary"] = f"Non verificabile: {e}"
        if not item["summary"]:
            if not item["intact"]:
                item["summary"] = "Non valida: il documento è stato modificato dopo la firma."
            elif not item["valid"]:
                item["summary"] = "Firma integra, ma il certificato non è valido (scaduto o non adatto)."
            elif item["changedAfter"]:
                item["summary"] = "Firma valida per la versione firmata; dopo sono state fatte altre modifiche."
            elif item["trusted"]:
                item["summary"] = "Valida: documento integro, certificato riconosciuto."
            elif item.get("later"):
                item["summary"] = "Integra: dopo questa firma sono state aggiunte solo altre firme o compilati campi."
            else:
                item["summary"] = ("Integra: il documento non è cambiato dopo la firma. Il certificato è personale "
                                   "(non emesso da un ente riconosciuto)." if item["selfSigned"] else
                                   "Integra: il documento non è cambiato dopo la firma. L'ente che ha emesso il "
                                   "certificato non è tra quelli riconosciuti da questo PC.")
        out.append(item)
    return out
