<p align="center">
  <img src="assets/icon.png" width="112" alt="Calamo">
</p>

<h1 align="center">Calamo PDF Studio</h1>
<p align="center"><b>Modifica i PDF come un documento Word. Anche ciò che è scritto a mano.</b></p>

<p align="center">
  <a href="../../releases/latest"><img src="https://img.shields.io/github/v/release/motthz/CalamoPDF?label=download&color=d9a84e" alt="Download"></a>
  <img src="https://img.shields.io/badge/Windows-10%20%7C%2011-1f2430" alt="Windows">
  <img src="https://img.shields.io/badge/licenza-AGPL--3.0-1f2430" alt="Licenza">
</p>

<p align="center"><img src="docs/screenshot.png" alt="Calamo PDF Studio" width="900"></p>

## Cosa fa

| | |
|---|---|
| ✍️ **Modifica diretta del testo** | Clicca su una frase e riscrivila sul posto. Font, dimensione, colore, grassetti e corsivi restano quelli originali; i paragrafi vanno a capo da soli e vengono riscritte solo le righe che cambiano. |
| 🖋️ **Scrittura a mano con la tua calligrafia** | Calamo legge il testo scritto a mano, impara la forma delle lettere di chi ha scritto e riscrive le nuove frasi con lo stesso tratto, dimensione e colore d'inchiostro. |
| 🧠 **Impara dalle tue correzioni** | Se legge male una parola, scrivi cosa c'è davvero: la correzione viene ricordata e applicata in automatico le volte successive, e le lettere vengono ri-etichettate. |
| 🔠 **Segna lettere e numeri** | Indica tu dove si trova ogni lettera e ogni numero di una riga scritta a mano: questi caratteri hanno la precedenza e restano anche negli altri documenti, così la calligrafia diventa sempre più completa. |
| 🖨️ **Distingue stampa e scrittura a mano** | Il testo stampato delle scansioni viene riconosciuto come tale (in grigio) e non sporca la calligrafia; si può comunque correggere e riscrivere. |
| 🔄 **Aggiornamenti automatici** | Quando su GitHub esce una nuova versione, Calamo la scarica in background e la installa alla chiusura. |
| 📄 **Esporta in Word ed Excel** | `.docx` con stili, immagini, tabelle e testo scritto a mano; `.xlsx` con le tabelle in fogli formattati (numeri e percentuali riconosciuti). |
| 🧰 **Tutto il resto** | Aggiungi testo, gomma per aree, trova e sostituisci, annulla/ripeti illimitato, ruota/inserisci/elimina pagine, tema scuro e chiaro, trascina e rilascia. |

<p align="center"><img src="docs/welcome.png" alt="Schermata iniziale" width="700"></p>

## Installazione

1. Scarica **`CalamoPDF-Setup-<versione>.exe`** dalla pagina [Releases](../../releases/latest) e avvialo.
2. In alternativa usa la versione portabile: **`CalamoPDF.exe`**, sempre dalla pagina [Releases](../../releases/latest) — non richiede installazione.

> Windows potrebbe mostrare l'avviso SmartScreen perché l'eseguibile non è firmato: *Ulteriori informazioni → Esegui comunque*.

Requisiti: Windows 10/11 a 64 bit con WebView2 (già presente su Windows 11). Per il riconoscimento serve un pacchetto lingua di Windows con OCR (italiano e inglese di solito sono già installati).

## Come si usa

| Strumento | Tasto | |
|---|---|---|
| Modifica | `M` | Clicca un blocco di testo per modificarlo; `Ctrl+Invio` applica, `Esc` annulla. Clicca una riga scritta a mano per correggerne la lettura o riscriverla. |
| Testo | `T` | Clicca un punto della pagina e scrivi nuovo testo. |
| A mano | `H` | Clicca e scrivi: il testo viene composto con la calligrafia appresa dal documento. |
| Gomma | `E` | Trascina per cancellare un'area (utile sulle scansioni). |

**Scrittura a mano:** premi **Leggi scrittura** (o *Analizza pagina* nel pannello *Scrittura*). Le righe scritte a mano vengono evidenziate in blu, quelle stampate in grigio, quelle dubbie in rosso con un `?`. Cliccando una riga puoi correggerne la lettura, dire se è *A mano* o *Stampato* e aprire **Segna lettere**: ogni riquadro è un carattere, clicca un riquadro e digita la lettera o il numero (passa da solo al successivo), trascina i bordi per spostarli, *Dividi*/*Unisci* per correggere la segmentazione. Nel pannello *La tua calligrafia* clicca una lettera per vederne gli esempi ed eliminare quelli sbagliati.

Al primo avvio un breve tutorial spiega le funzioni principali (si può rivedere da *Impostazioni → Guida*).

## Come funziona

- **Testo digitale** — `PyMuPDF` estrae blocchi, righe e span con font e stile. Alla modifica il testo viene rimosso con una *redaction* mirata e riscritto con il font incorporato nel PDF (se contiene i glifi necessari) oppure con il font di sistema più simile.
- **Scrittura a mano** — l'OCR di Windows (`Windows.Media.Ocr`) fornisce una prima lettura; nelle parole fatte di cifre le lettere simili a numeri (`O`→`0`, `l`→`1`, `S`→`5`…) vengono corrette. Ogni riga viene classificata come scritta a mano o stampata (allineamento della linea di base, regolarità delle altezze, lettere unite, colore dell'inchiostro): solo le righe a mano insegnano la calligrafia. Le parole vengono segmentate in lettere usando il testo riconosciuto (e corretto) come etichetta, oppure i riquadri segnati dall'utente. Il nuovo testo viene composto con le lettere originali, con piccole variazioni naturali, e inserito al posto della riga dopo aver coperto l'originale con il colore della carta.
- **Memoria** — correzioni, lettere segnate (`calligrafia.json`) e impostazioni sono salvate in `%APPDATA%\CalamoPDF`.
- **Aggiornamenti** — all'avvio l'app legge l'ultima release di GitHub; se è più recente scarica l'installer (copia installata) o il nuovo `CalamoPDF.exe` (copia portabile) e lo applica dopo la chiusura.

## Pubblicare una nuova versione

1. Aggiorna `VERSION` in `app/main.py` (e `version_info.txt`).
2. Crea e invia il tag: `git tag v1.1.0 && git push origin v1.1.0`.
3. Il workflow *Release* compila `CalamoPDF.exe` e l'installer e li pubblica nella release: le app già installate si aggiornano da sole.

## Sviluppo

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python app\main.py            # avvia l'app
.\.venv\Scripts\python tools\smoke_test.py    # test del motore
.\build.ps1                                   # crea CalamoPDF.exe e l'installer (Inno Setup 6)
```

Struttura: `app/engine` (motore PDF, OCR, calligrafia, esportazione), `app/ui` (interfaccia HTML/CSS/JS in WebView2 tramite pywebview), `tools/` (build, test, server di sviluppo).

## Licenza

[GNU AGPL-3.0](LICENSE) — il progetto usa PyMuPDF/MuPDF, distribuiti con la stessa licenza.
