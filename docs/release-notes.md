## Novità di Calamo PDF Studio 1.4.0

**Tutti gli strumenti per lavorare sui PDF, oltre alla scrittura a mano.**

**Unisci e dividi PDF interi** (pulsante nella barra o nella schermata iniziale, anche senza documento aperto)
- **Unisci** più PDF in un nuovo file: li metti in ordine trascinandoli, per ognuno puoi prendere solo alcune pagine ("1-3, 5"), puoi aggiungere il documento aperto con le sue modifiche. Ogni file diventa un segnalibro, e i segnalibri dei file restano.
- **Dividi** un PDF in più file: ogni N pagine, per intervalli ("1-3, 4-6, 7-"), una pagina per file, un file per capitolo (segnalibri) o in file al massimo di N MB (per le email).

**Prendi e trascina gli elementi della pagina**
- Con **Modifica** (M) o **Oggetti** (O) premi su un testo e trascinalo: viene spostato con il suo carattere, la sua dimensione e il suo colore, e resta modificabile e cercabile. Un clic senza trascinare continua a modificarlo.
- Lo stesso vale per le **scritte delle scansioni** (a mano o stampate, lette da Calamo): si spostano solo le lettere; la carta, l'evidenziatore o la tabella sotto restano dove sono, e al loro posto la carta viene ricostruita.
- Firme, immagini (anche quelle già presenti nel PDF), note e forme si trascinano con **Oggetti**.

**Pagine**
- **Riordina** trascinando le miniature a sinistra.
- **Inserisci** altri PDF dopo la pagina corrente, **duplica** una pagina, **estrai** pagine in un nuovo PDF (es. "1-3, 5"), **elimina** più pagine insieme (scheda *Pagina*).

**Annota** (tasto A)
- **Evidenzia, sottolinea e barra** trascinando sul testo: il segno segue le righe, anche sulla scrittura a mano letta da Calamo e sulle scansioni.
- **Note** (clicca dove lasciarla), **rettangoli, ellissi e frecce**, in 7 colori.
- Nuova scheda **Commenti**: tutti i segni e le note del documento, con salto alla pagina, modifica del testo ed eliminazione.

**Firma e immagini** (tasto F)
- **Disegna la firma** con mouse o penna, oppure **prendila da una foto**: lo sfondo della carta viene tolto e resta solo l'inchiostro. Le firme restano salvate per i prossimi documenti; si mettono con un clic.
- **Inserisci immagini** con un clic sulla pagina.
- Strumento **Oggetti** (tasto O): sposta, ridimensiona (in proporzione), ritaglia, sostituisci ed elimina firme, immagini (anche quelle già presenti nel PDF), note e forme; doppio clic su una nota per riscriverla.

**Oscura** (tasto X)
- Trascina su un'area: testo e immagini sotto vengono **eliminati davvero** dal PDF, non solo coperti.
- Scheda *Documento*: **cerca e oscura in un colpo** codici fiscali, IBAN, email, telefoni, carte di credito e qualsiasi testo (es. un nome), anche nella scrittura a mano letta; volendo toglie anche autore e proprietà del file.

**Documento**
- **Intestazione, piè di pagina e numeri di pagina** con le variabili {n}, {N}, {data}, {file}; **filigrana** (es. BOZZA) con colore e trasparenza.
- **Confronta con un'altra versione**: le differenze vengono segnate sulla pagina e il testo aggiunto/tolto elencato pagina per pagina; per le scansioni il confronto è sull'immagine.
- **Copia leggera** (immagini ricompresse, tre livelli di qualità) e **copia protetta** con password di apertura e limiti a stampa, copia e modifiche.
- I PDF protetti da password ora si aprono: Calamo chiede la password.

**Correzioni**
- Il testo scritto o modificato *dopo un salvataggio* poteva risultare illeggibile quando lo si copiava o cercava (a vista era giusto): ora resta sempre corretto.
- Un evidenziatore giallo molto acceso veniva scambiato per inchiostro, e riscrivendo una scritta evidenziata il giallo spariva: ora resta.

## Calamo PDF Studio 1.3.2

**Si modificano solo le lettere, lo sfondo resta com'era.**
- Quando riscrivi una scritta, Calamo riconosce pixel per pixel quali sono le lettere e quali lo sfondo: carta, evidenziatore, bordi delle tabelle, righe del quaderno, sottolineature.
- Viene ricostruito solo il posto delle lettere: l'**evidenziatore continua** (anche dove finisce a metà parola), le **tabelle non si deformano** e le righe che attraversavano le lettere proseguono.
- Il nuovo testo resta **dentro la sua cella**: se è più lungo va a capo nella cella o viene stretto, senza superare il bordo.
- Le lettere imparate dalla pagina e la misura della riga non includono più pezzi di bordi o di sottolineature.
- Funziona anche quando il riquadro letto è più piccolo della scritta (lettere alte, code, pagine ruotate): vengono tolte le lettere intere, e il testo nuovo ha la grandezza della scritta vera, non del riquadro.
- Corretto un difetto della 1.3.1: il lettore scambiava le aste alte delle lettere (l, t) per bordi di tabella.

## Calamo PDF Studio 1.3.1

**Correggere cosa Calamo considera scrittura.**
- **"Non è una scritta"**: nel riquadro di una riga c'è il cestino. La riga sparisce e non viene più proposta, anche rileggendo la pagina (Ctrl+Z per ripristinarla).
- **Segna scritta** (tasto R): trascina un riquadro intorno a una scritta che Calamo non ha riconosciuto. Viene letta e aggiunta alla scrittura della persona che l'ha scritta; se non si riesce a leggerla, scrivi tu cosa c'è. Le scritte segnate restano anche rileggendo la pagina.

**Moduli e tabelle scritti a mano.**
- I bordi delle tabelle vengono riconosciuti anche se disegnati a mano o storti nella foto, e non vengono più letti come lettere ("I", ")").
- Ogni cella viene letta da sola: niente più testi di celle vicine uniti, né righe diverse della stessa cella mescolate.
- Il lettore della scrittura a mano riceve righe ripulite (sfondo della carta appiattito, senza pezzi delle righe sopra e sotto) e legge anche l'inchiostro che l'OCR di Windows non ha letto.
- Trattini e pezzi di bordo isolati non vengono più proposti come scritte.
- Il lettore si scarica anche da un mirror alternativo quando HuggingFace non è raggiungibile.

## Calamo PDF Studio 1.3.0

**Un lettore dedicato alla scrittura a mano.**
- Nelle Impostazioni puoi scaricare (una sola volta, circa 60 MB) un modello che legge le righe scritte a mano molto meglio dell'OCR di Windows; poi funziona offline. Nei test sbaglia il 3% dei caratteri contro il 44% dell'OCR di Windows.
- Un **dizionario italiano** corregge gli errori tipici di lettura ("Ottinoo" → "Ottimo", "effro" → "entro") senza stravolgere nomi e cognomi.
- Quando correggi una parola, la stessa correzione viene proposta nelle altre righe della **stessa persona**.
- Le scansioni **storte** vengono raddrizzate prima della lettura.
- La lettura delle pagine avviene **in background**: puoi continuare a lavorare e interromperla dalla barra in basso.
- **Ricerca tollerante**: cercando un nome trovi anche le righe a mano lette con qualche lettera sbagliata (evidenziate con il bordo tratteggiato).

**Scrivere a mano in modo più naturale.**
- **Paragrafi**: Maiusc+Invio va a capo; il testo lungo continua sulle righe sotto, con l'interlinea di quella persona, solo dove la pagina è libera.
- Le lettere vengono scelte e allineate perché il tratto di uscita di una si colleghi all'entrata della successiva.
- La scrittura cancellata viene coperta **ricostruendo la carta** (righe del quaderno, ombre, grana della scansione) invece che con un rettangolo di colore; vale anche per la gomma.
- **Unisci e dividi le scritture**: trascina una scrittura su un'altra se sono la stessa persona, o dividila in due se ne mescola due.

**Più veloce.** L'annulla salva solo la pagina modificata (molto più leggero sui documenti lunghi) e la lettura di una pagina è più rapida.

**Coerenza tra le scritture.**
- **Più persone sullo stesso foglio**: Calamo separa le righe scritte da persone diverse (colore dell'inchiostro, spessore del tratto, inclinazione, dimensione) e i caratteri stampati. Ogni riga viene riscritta solo con le lettere della *sua* scrittura: niente più calligrafie mescolate. Ogni persona ha un colore sulla pagina; nel riquadro della riga puoi spostarla in un'altra scrittura se serve.
- **Stampato o a mano, deciso per tutta la scrittura**: se una scrittura viene presa per stampata (o viceversa), la correggi una volta e vale per tutte le sue righe.
- **Stesse dimensioni e stessa linea**: le lettere sono misurate rispetto alla linea di base e all'altezza delle minuscole, e il testo nuovo si appoggia esattamente sulla riga che sostituisce, con la stessa altezza.
- **Parole e sillabe riusate**: se la frase nuova contiene parole o pezzi di parola già scritti da quella persona, viene usato il tratto originale, con i collegamenti naturali tra le lettere.

**Stampato identico alla scansione.**
- Lo stampato riscritto ora usa le lettere ritagliate dalla scansione stessa (stesso carattere, spaziatura, colore e sfocatura); per le lettere che mancano sceglie il font di sistema più simile, con lo stesso spessore. Resta cercabile come prima.

**Lettura più completa.**
- Una seconda lettura con contrasto migliorato recupera la scrittura chiara (matita, penne scariche, ombre delle foto fatte col telefono).
- L'inchiostro che resta illeggibile viene segnato in arancione: cliccalo e scrivi cosa c'è.

**Altro**
- Le scorciatoie da tastiera tornano a funzionare dopo aver chiuso un riquadro con Esc.

### Download
- **CalamoPDF-Setup-1.4.0.exe** — installer per Windows.
- **CalamoPDF.exe** — versione portabile.

Chi ha già una versione precedente riceve questa in automatico.
