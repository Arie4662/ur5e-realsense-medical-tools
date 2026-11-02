"""Export only reviewed labels using the same rules as the annotation UI."""
import argparse
import json
from pathlib import Path
from annotation_workspace import AnnotationWorkspace


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['all', 'hemostat'], default='hemostat')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    workspace = AnnotationWorkspace(root / 'data' / 'medical_tools_dataset',
                                    root / 'dataset_annotations' / 'medical_tools')
    print(json.dumps(workspace.export(args.mode), ensure_ascii=False, indent=2))
