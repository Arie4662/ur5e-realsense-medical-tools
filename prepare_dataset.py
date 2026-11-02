"""Verify the bundled images and bind annotations to this checkout's location."""
import hashlib
import json
from pathlib import Path
from annotation_workspace import CLASSES, AnnotationWorkspace, write_json


def prepare():
    root = Path(__file__).resolve().parent
    source = root / 'data' / 'medical_tools_dataset'
    project = root / 'dataset_annotations' / 'medical_tools'
    manifest = json.loads((project / 'classification' / 'manifest.json').read_text())
    expected = {row['relative_path']: row for row in manifest['images']}
    actual = {p.relative_to(source).as_posix() for p in source.rglob('*.jpg')}
    if actual != set(expected):
        raise ValueError('Dataset image list differs from the saved classification manifest')
    for relative, row in expected.items():
        path = (source / relative).resolve(strict=True)
        if source.resolve() not in path.parents:
            raise ValueError('Image path leaves the bundled dataset')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('Image checksum mismatch: ' + relative)
    identity = {'source': str(source.resolve()), 'classes': CLASSES, 'schema': 1}
    config = project / 'project.json'
    if config.exists() and json.loads(config.read_text()) != identity:
        raise ValueError('Existing project.json points elsewhere; review it before rebinding')
    if not config.exists():
        write_json(config, identity)
    workspace = AnnotationWorkspace(source, project)
    print(json.dumps({'verified_images': len(workspace.entries),
                      'source': str(source), 'annotation_folder': str(project)}, ensure_ascii=False))


if __name__ == '__main__':
    prepare()
