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
| 🔠 **Segna lettere e numeri** | Indica tu dove si trova ogni lettera e ogni numero di una riga scritta a mano: questi caratteri hanno la precedenza, così la calligrafia della pagina diventa più completa. |
| 👥 **Più scritture sullo stesso foglio** | Calamo separa le righe di persone diverse (colore dell'inchiostro, spessore del tratto, inclinazione, dimensione) e i caratteri stampati: ogni riga viene riscritta solo con le lettere della sua scrittura, mai mescolate. Una scrittura si può rinominare, *ricordare* con un nome e usare su altre pagine o documenti quando lo scegli tu. |
| 🖨️ **Stampato identico alla scansione** | Lo stampato riscritto usa le lettere ritagliate dalla scansione stessa, con la stessa spaziatura, linea di base e dimensione; le lettere mancanti usano il font di sistema più simile, con lo stesso spessore e la stessa sfocatura. Resta cercabile. |
| 🔎 **Lettura più completa** | Una seconda lettura con contrasto migliorato recupera la scrittura chiara (matita, penne scariche, ombre delle foto); l'inchiostro che resta illeggibile viene segnalato in arancione per scriverne la lettura. |
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

**Scrittura a mano:** premi **Leggi scrittura** (o *Analizza pagina* nel pannello *Scrittura*). Le righe scritte a mano vengono colorate (un colore per persona), quelle stampate in grigio, quelle dubbie in rosso con un `?`, l'inchiostro non letto in arancione. Cliccando una riga puoi correggerne la lettura, scegliere a quale *scrittura* appartiene, dire se quella scrittura è *A mano* o *Stampata* (vale per tutte le sue righe) e aprire **Segna lettere**: ogni riquadro è un carattere, clicca un riquadro e digita la lettera o il numero (passa da solo al successivo), trascina i bordi per spostarli, *Dividi*/*Unisci* per correggere la segmentazione. Nel pannello *Scritture di questa pagina* scegli una scrittura per vederne le lettere (clicca una lettera per vederne gli esempi ed eliminare quelli sbagliati), rinominarla, *ricordarla* con un nome o completarla con una calligrafia ricordata (solo per le lettere che le mancano). Con lo strumento *A mano* puoi anche cliccare una scritta esistente per riscriverla.

Al primo avvio un breve tutorial spiega le funzioni principali (si può rivedere da *Impostazioni → Guida*).

## Come funziona

- **Testo digitale** — `PyMuPDF` estrae blocchi, righe e span con font e stile. Alla modifica il testo viene rimosso con una *redaction* mirata e riscritto con il font incorporato nel PDF (se contiene i glifi necessari) oppure con il font di sistema più simile.
- **Scrittura a mano** — l'OCR di Windows (`Windows.Media.Ocr`) fornisce una prima lettura; nelle parole fatte di cifre le lettere simili a numeri (`O`→`0`, `l`→`1`, `S`→`5`…) vengono corrette. Una seconda lettura su un'immagine con sfondo appiattito e contrasto aumentato aggiunge le parole mancate; l'inchiostro rimasto senza lettura diventa una riga "non letta". Per ogni riga si misurano colore dell'inchiostro, spessore del tratto, inclinazione, dimensione e un punteggio mano/stampa; le righe vengono raggruppate in *scritture* (persone o caratteri tipografici) e il tipo mano/stampa si decide per scrittura, così resta coerente. Ogni scrittura ha la sua libreria: lettere, parole intere e spaziature, misurate rispetto alla linea di base e all'altezza delle minuscole (non al riquadro della parola). Il nuovo testo riusa le parole e le sillabe già scritte da quella persona (con i collegamenti veri tra le lettere), poi le singole lettere, poi il font più simile per le mancanti; viene appoggiato sulla linea di base della riga originale, con la stessa altezza, dopo aver coperto l'originale ricostruendo la carta (ogni riga di pixel prosegue il colore trovato a sinistra e a destra, con la grana della scansione).
- **Lettore della scrittura a mano** — opzionale, scaricato al primo uso in `%APPDATA%\CalamoPDF\modelli`: TrOCR small handwritten (Microsoft, licenza MIT) in formato ONNX, eseguito con onnxruntime sulla CPU. Ogni riga a mano viene ripulita (inchiostro nero su bianco) e letta per intero; tra lettura OCR e lettura del modello, corrette con il dizionario italiano (`app/engine/data/it_words.txt`, da FrequencyWords/OpenSubtitles, CC BY-SA 4.0), vince quella più sensata in italiano. Le scansioni storte vengono raddrizzate prima dell'OCR.
- **Memoria** — correzioni di lettura, calligrafie ricordate per nome (`calligrafie.json`) e impostazioni sono salvate in `%APPDATA%\CalamoPDF`. Le lettere apprese da una pagina non vengono salvate da sole.
- **Pagine ruotate** — le scansioni da telefono hanno spesso `/Rotate` e un cropbox spostato: le coordinate mostrate a schermo (e lette dall'OCR) vengono convertite in quelle della pagina con la stessa trasformazione che MuPDF usa per il rendering, così le modifiche finiscono esattamente dove le vedi.
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
