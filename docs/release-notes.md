## Novità di Calamo PDF Studio 1.5.2

**Esportazione vera, anche dalle scansioni.** Word, Excel, PowerPoint, pagina web e testo non mettono più la pagina come un'unica immagine:
- le pagine scansionate vengono **lette in automatico** prima di esportare (con la barra di avanzamento);
- il **testo** diventa testo modificabile, le **tabelle** diventano tabelle vere (righe e colonne prese dalla griglia disegnata sul foglio, anche se storta o ondulata), le **immagini e la grafica** (timbri, loghi, segni) vengono ritagliate a parte con lo sfondo trasparente;
- in PowerPoint e nella pagina web lo sfondo resta la carta della scansione, ma senza le scritte, che stanno sopra come testo.

**Lettura più precisa, frase per frase.** Provata su 8 foto dello stesso modulo compilato con grafie diverse (le più difficili scritte apposta male):
- le righe vengono tagliate sui **bordi delle celle**: le parole di due colonne vicine non si mescolano più in una sola frase;
- **punteggiatura e date** sistemate (spazi prima e dopo virgole e punti, apostrofi, "I'" letto al posto di "l'", date come 18/02/2024);
- il lettore della scrittura a mano non inventa più **parole inglesi** o sequenze ripetute quando la grafia è illeggibile;
- celle lette correttamente: dal 34% al 44% in media (sui moduli scritti in modo leggibile dal 50–70% fino all'88%); parole trovate dal 45% al 50%, parole giuste fra quelle lette dal 41% al 52%.

**Ridimensiona ogni elemento.** Passando sopra un testo, una scritta, una tabella, un disegno, un'immagine o un campo compare una maniglia nell'angolo: trascinala per ingrandire o rimpicciolire (le proporzioni restano, Maiusc per cambiarle). Il testo cambia dimensione del carattere e resta modificabile.

**Elementi dentro le tabelle.** Su una scansione non ancora letta, ogni scritta di una cella e ogni segno (un cerchio, una firma, un timbro) si prende da solo; la tabella intera si prende dal bordo.

**Stampa con tre possibilità**: *Stampa…* (con la finestra delle impostazioni), *Stampa tutto subito* e *Stampa questa pagina subito* sulla stampante predefinita, senza finestre.

**Niente più blocchi e chiusure improvvise.**
- La libreria dei PDF non è fatta per lavorare da più parti contemporaneamente: lettura in sottofondo, miniature ed esportazioni ora fanno a turno. Era la causa dei crash casuali.
- Chiudere la finestra con un documento modificato poteva bloccare l'app.
- Le pagine molto grandi vengono disegnate a una risoluzione limitata (prima potevano esaurire la memoria); le pagine lontane da quella che guardi liberano la memoria.
- Se succede comunque un errore grave viene scritto in `errori.log` nella cartella dei dati di Calamo.

**Correzioni.** Un'immagine spostata o ridimensionata due volte poteva sparire.

## Novità di Calamo PDF Studio 1.5.1

**Audit completo: ogni funzione provata come un utente, con screenshot, e spinta al limite** (documenti da 500 e 3000 pagine, scansioni da 77 MB, moduli con 600 campi, input sbagliati di proposito).

**Molto più veloce sui documenti grandi** (500 pagine):
- **Cerca dati sensibili**: da oltre 6 minuti (l'app sembrava bloccata) a circa 7 secondi; l'elenco mostra i primi 300 risultati ma vengono oscurati tutti.
- **Salva**: da 15–60 secondi a meno di un secondo (l'unione degli oggetti doppi, lentissima sui file grandi, ora si fa solo sui documenti piccoli).
- **Modificare, ruotare o ritagliare una pagina** di un documento con segnalibri: da circa 2 secondi a 0,05 (l'annulla salva solo la pagina cambiata).
- **Esporta in testo**: da 12 a meno di un secondo. **PDF/A**: circa 4 volte più veloce, con la barra di avanzamento.
- Le **scansioni** arrivano a schermo in JPEG invece che in PNG: fino a 5 volte meno dati per pagina, scorrimento e zoom più fluidi.
- La **memoria dell'annulla** ha un limite: 12 operazioni su una scansione da 77 MB occupavano quasi 1 GB, ora al massimo 400 MB (le copie più vecchie vengono scartate).

**Correzioni.**
- Un rettangolo o una freccia disegnati sopra una tabella venivano presi per linee della tabella: *Modifica tabella* ed *Esporta in Excel* sbagliavano righe e colonne.
- Il testo scritto nei campi dei moduli compariva come testo modificabile con *Modifica* (modificarlo scriveva testo sotto il campo) e poteva diventare un segnalibro.
- Passando da un documento grande a uno piccolo comparivano errori "page … not in document".
- Dopo aver chiuso la ricerca o la finestra Strumenti, Ctrl+O e le altre scorciatoie non funzionavano più; ora le scorciatoie con Ctrl funzionano da qualsiasi campo.
- F5 non avviava la presentazione se il cursore era in un menu a tendina.
- La barra dei suggerimenti copriva l'inizio della pagina e la barra di ricerca si sovrapponeva ad essa.
- Trascinando una miniatura verso il bordo, l'elenco delle pagine ora scorre da solo (prima non si poteva portare una pagina oltre la parte visibile).
- Pagine da estrarre scritte male e protezione senza password ora vengono segnalate subito, prima di chiedere dove salvare.
- Messaggi più chiari ("1 pagina" invece di "1 pagine", OCR di Windows non disponibile) e niente più notifiche uguali ripetute.

**Test.** `tools/stress_test.py` misura i tempi di ogni operazione sui documenti grandi; i nuovi casi trovati dall'audit sono nei test automatici.

## Novità di Calamo PDF Studio 1.5.0

**Tutti gli strumenti dei grandi programmi per PDF, più facili da trovare.**
- **Strumenti** (Ctrl+K): una finestra con tutte le funzioni divise per argomento e una casella di ricerca ("comprimi", "firma", "word"…). Se una funzione lavora su un documento e non ne hai aperto nessuno, Calamo ti chiede prima quale.
- **Schermata iniziale** con le attività più comuni: modifica, unisci, dividi, comprimi, converti, PDF in Word, compila, firma.
- **Barra più semplice**: gli strumenti principali hanno il nome sempre visibile, gli altri sono nel menu **Altro**. Sotto la barra un **suggerimento** spiega come si usa lo strumento scelto.
- **Pannelli a destra riordinati**: Scrittura, Pagine, Moduli, Sicurezza, Documento, Commenti. Impostazioni e guida nei pulsanti ⚙ e ? in alto; guida rinnovata.

**Moduli.** Compila i campi direttamente sulla pagina (Compila, tasto C); crea campi di testo, caselle, scelte, menu a tendina e spazi per la firma trascinando un riquadro; **Riconosci i campi** trova da solo righe, puntini e caselle di un modulo pensato per la carta e li rende compilabili, con il nome preso dal testo accanto; **Appiattisci** campi e commenti.

**Firma digitale con certificato** (PAdES): con un file `.p12`/`.pfx` o con un certificato personale creato in Calamo, in un nuovo riquadro o in un campo firma già presente, con marca temporale facoltativa. **Verifica firme**: chi ha firmato, quando, e se il documento è cambiato dopo la firma. Salvare sopra un PDF firmato ora avvisa che le firme non sarebbero più valide.

**Pagine.** Ritaglia trascinando un riquadro, togliendo dei millimetri o solo i margini bianchi; cambia formato (A4, A3, A5, Lettera, Legale); più pagine per foglio e **opuscolo** da piegare; **segnalibri** nella barra a sinistra (anche creati dai titoli); **collegamenti** a siti web o ad altre pagine.

**Documento.** Proprietà (titolo, autore, oggetto, parole chiave, lingua); **verifica** PDF/A e accessibilità con il pulsante per sistemare ogni punto; copia **PDF/A** per l'archiviazione e gli uffici pubblici; numerazione **Bates**; **bianco e nero**; togli la **password** (copia o documento aperto).

**Esporta** anche in **PowerPoint** (testo modificabile sopra lo sfondo della pagina), **pagina web** e **testo**. **Stampa** con Ctrl+P. **Ripara** i PDF danneggiati. **Elabora più file insieme**: comprimi, numera, filigrana, ruota, proteggi o togli la password, PDF/A, appiattisci, bianco e nero, ripara.

**Correzioni.** Un PDF aperto con la password veniva salvato senza protezione: ora resta protetto con la stessa password e gli stessi permessi (anche dopo Annulla). Salvare un documento senza modifiche non riscrive più il file.

## Novità di Calamo PDF Studio 1.4.2

**Trascina qualsiasi elemento della pagina.** Con lo strumento *Modifica* (o *Oggetti*) si prende e si sposta ogni cosa, non solo le scritte:
- **disegni e tabelle** fatti di linee e riquadri: la tabella si prende dal bordo e si porta dietro tutto il suo testo, che resta modificabile;
- **immagini** e **campi dei moduli** compilabili;
- **note, forme, firme e timbri** anche senza cambiare strumento;
- sulle **scansioni**, la grafica dell'immagine (un timbro, un logo, un riquadro, una linea): Calamo la stacca dalla carta, ricostruisce la carta sotto (con la sua grana) e la riappoggia dove la lasci, con i suoi colori.
Funzionano anche le guide magnetiche, l'allineamento di più elementi (Maiusc+clic) e Ctrl+Z.

**Riscrittura a mano più leggibile sui moduli difficili.**
- Calamo ora **misura** quanto è leggibile ciò che riscrive: il lettore della scrittura a mano rilegge la frase composta e la confronta con quella chiesta. Fra alcune composizioni possibili sceglie quella che si legge meglio (anche per l'anteprima).
- **Impara solo dalle righe sicure**: una riga che il lettore e l'OCR leggono in modo diverso, o misurata male (due righe di una cella fitta lette come una), non insegna più lettere sbagliate.
- Le righe di una cella fitta che l'OCR leggeva come una sola vengono **separate** e lette una per una.
- Le linee della tabella che attraversano le lettere non finiscono più dentro le lettere imparate.
- **Corsivo legato**: i tagli tra le lettere sono scelti tutti insieme, tenendo conto della larghezza di ogni lettera (come la scrive quella persona, o come la segni tu con *Segna lettere*) e di dove passa meno inchiostro. Gli esempi di una lettera molto diversi dagli altri (o da quelli segnati da te) vengono scartati.
- La stessa persona non viene più divisa in tante "scritture" per poche righe scritte un po' diverse.
- Sui cinque moduli reali di prova l'errore di rilettura delle frasi riscritte scende in media da 0,50 a circa 0,35 (0 = letta perfettamente). I moduli scritti in fretta e molto fitti restano i più difficili: lì *Segna lettere* fa la differenza.

**Stampato o a mano: decidi tu nei casi dubbi.** Se alcune righe sembrano stampate dentro una scrittura a mano (o il contrario), il pannello *Scrittura* lo segnala con **Mostra**, **Separa** e **Vanno bene così**.

**Controlli su Windows reale.** I test automatici ora provano anche il trascinamento col mouse vero (anche delle pagine fra due documenti affiancati), la traduzione online, la stampa in PDF con Word dove c'è e le voci di sistema per la lettura ad alta voce.

## Calamo PDF Studio 1.4.1

**Un aspetto più sobrio e professionale.**
- Niente più pulsanti dorati con sfumature e aloni luminosi: il pulsante principale è a tinta unita (chiaro sul tema scuro, scuro sul tema chiaro), gli altri sono piatti con bordi sottili.
- Colori grafite neutri con un solo accento blu inchiostro, usato solo per selezione e focus.
- Logo e titoli a tinta unita, niente testo sfumato né sfondi con bagliori; menu e riquadri non più trasparenti e sfocati.
- Titoli delle sezioni in caratteri normali invece che in maiuscolo spaziato, angoli meno arrotondati, ombre più leggere.
- Schermata iniziale più essenziale, con le funzioni in un elenco invece che in schede.

**Correzioni dopo un controllo completo dell'app, schermata per schermata.**
- Con più documenti aperti in schede, la barra delle opzioni degli strumenti (Annota, Firma, Oggetti) e l'inizio della pagina finivano in parte sotto le schede: ora restano sempre visibili, su una sola riga.
- Con la finestra stretta (fino a 1000 px) i pulsanti di chiusura, "Esporta" e il pannello di destra non vengono più tagliati: "Leggi scrittura" ed "Esporta" diventano icone.
- Le schede del pannello di destra non si toccano più; corretto "1 pagine su 3 diverse" nel confronto; niente più bordo di selezione sui pulsanti dopo un clic.
- **Scrivere a mano più pulito**: le lettere imparate non contengono più pezzi delle lettere vicine (scrittura inclinata), il piccolo stampato dei moduli o macchioline della scansione; le parole lette male non vengono più riusate tali e quali, e le sillabe si riusano solo dove il taglio tra le lettere è sicuro. Su moduli reali le frasi nuove escono molto più leggibili.

## Calamo PDF Studio 1.4.0

**Tutti gli strumenti per lavorare sui PDF, oltre alla scrittura a mano.**

**Più documenti insieme**
- **Schede**: ogni PDF si apre in una sua scheda sopra la pagina (aprirne uno nuovo non chiude quello attuale). Ctrl+Tab passa alla successiva, Ctrl+W chiude; alla chiusura Calamo chiede di salvare ogni documento modificato.
- **Affianca**: il pulsante in alto nella colonna delle pagine mostra le pagine di un altro documento aperto; trascina le pagine da una colonna all'altra per copiarle tra i due PDF.

**Rendi cercabile (OCR)** (scheda *Documento*)
- Calamo legge tutte le pagine scansionate, anche la scrittura a mano, e aggiunge un testo invisibile al posto giusto: il PDF diventa cercabile e il testo si può copiare, anche in altri programmi.

**Converti**
- **In PDF**: Word, Excel, PowerPoint, immagini e foto (raddrizzate), pagine web (anche da indirizzo), testo. Con Microsoft Office o LibreOffice installato l'impaginazione è identica; senza, Calamo converte da solo testo, titoli e tabelle di Word ed Excel. Più file diventano un unico PDF.
- **Da PDF a immagini**: PNG o JPG, alla qualità che serve.

**Tabelle modificabili** (scheda *Pagina*)
- Cambia il testo delle celle con il carattere della tabella (va a capo o si stringe nella cella), **aggiungi** una riga sotto quella scelta o **togline** una: le righe sotto scorrono, il bordo della tabella si allunga o si accorcia e il testo sotto la tabella si sposta.

**Griglia, guide e allineamento** (strumento *Oggetti*)
- **Guide magnetiche**: mentre trascini, testi e oggetti si agganciano ai bordi e ai centri degli altri e al centro della pagina.
- **Griglia con righello** in centimetri (aggancio ogni 0,5 cm).
- **Maiusc+clic** per scegliere più elementi: allineali (sinistra, centro, destra, alto, centro, basso) o distribuiscili a distanze uguali; un elemento solo si allinea alla pagina.

**Traduci** (scheda *Documento*)
- Crea una copia tradotta in un'altra lingua in una nuova scheda: ogni testo viene riscritto nel suo riquadro con il suo carattere (più piccolo se la traduzione è più lunga) e la scrittura a mano letta da Calamo con la stessa calligrafia. Serve la connessione a internet (servizio di Google).

**Leggi e presenta**
- **Lettura ad alta voce** con le voci di Windows: legge la pagina nell'ordine giusto (anche a due colonne e la scrittura a mano letta), evidenzia la frase letta e prosegue con le pagine successive; velocità e voce regolabili.
- **Presentazione a schermo intero** (F5): una pagina alla volta, frecce o clic per andare avanti, Esc per uscire.

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
- Gli spazi del testo scritto da Calamo con i font di Windows (Arial, Times, Calibri…) venivano registrati come "spazi non separabili": copiando il testo o cercando più parole insieme non si trovava nulla. Ora sono spazi normali.
- La ricerca trova anche le parole scritte con legature tipografiche ("ﬃ", "ﬂ"), comuni nei PDF fatti da Word.
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
- **CalamoPDF-Setup-1.4.1.exe** — installer per Windows.
- **CalamoPDF.exe** — versione portabile.

Chi ha già una versione precedente riceve questa in automatico.
