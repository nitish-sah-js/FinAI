import sys
from pathlib import Path

# Ensure services/vectordb and packages/copilot_common are on sys.path
root = Path(__file__).resolve().parent.parent.parent.parent
services_vectordb = root / "services" / "vectordb"
packages_common = root / "packages" / "copilot_common"

for p in (services_vectordb, packages_common, root):
    str_p = str(p)
    if str_p not in sys.path:
        sys.path.insert(0, str_p)
