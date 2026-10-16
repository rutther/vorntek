"""Build the optional corporate website from versioned content and templates."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from corporate_site import ExampleWebsite, main

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog',type=Path,default=Path(__file__).resolve().parents[1]/'docs/vorntekDemo/catalog.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    main(catalog=json.loads(args.catalog.read_text(encoding='utf-8')),output=args.output)
