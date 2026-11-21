"""Run a command script with the test guard installed before imports."""
from pathlib import Path
import runpy
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
from guard import install
install()
path = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(path,run_name='__main__')
