## Novità di Calamo PDF Studio 1.3.1

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
- **CalamoPDF-Setup-1.3.1.exe** — installer per Windows.
- **CalamoPDF.exe** — versione portabile.

Chi ha già una versione precedente riceve questa in automatico.
