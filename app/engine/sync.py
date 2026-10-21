"""One lock for all the work on PDFs. PyMuPDF (MuPDF) must not be used by two threads
at the same time, not even on two different documents: the interface calls the engine
from many threads at once (a page being drawn, a thumbnail of another tab, a file being
merged), and two of them meeting inside MuPDF can crash the whole app."""
import threading

PDF_LOCK = threading.RLock()
