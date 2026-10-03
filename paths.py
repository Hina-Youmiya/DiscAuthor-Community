"""Resource paths and isolated public-edition project storage."""
from pathlib import Path
import os
ROOT = Path(__file__).resolve().parent
DATA_ROOT = Path(os.environ.get('DISCAUTHOR_DATA_DIR') or
                 Path.home()/'Library/Application Support/DiscAuthor Community')
DATA_ROOT.mkdir(parents=True, exist_ok=True)
