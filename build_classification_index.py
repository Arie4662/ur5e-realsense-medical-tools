"""Record image-level classes from the user's folders, without approving boxes."""
import csv
import io
import json
from pathlib import Path
from datetime import datetime, timezone
from annotation_workspace import AnnotationWorkspace, CLASSES, write_json


def build():
    project = Path(__file__).resolve().parent/'dataset_annotations'/'medical_tools'
    source = (Path(__file__).resolve().parent / 'data' / 'medical_tools_dataset')
    workspace = AnnotationWorkspace(source, project)
    rows = []
    for entry in workspace.entries.values():
        cls = CLASSES[entry['source_class']]
        assert Path(entry['file']).parts[0] == cls['name']
        rows.append({'image_id': entry['id'], 'relative_path': entry['file'],
                     'class_id': cls['id'], 'class_name': cls['name'], 'class_slug': cls['slug'],
                     'sha256': entry['sha256'], 'label_source': 'user_assigned_folder'})
    assert len({row['image_id'] for row in rows}) == len(rows)
    counts = [{**cls, 'images': sum(r['class_id'] == cls['id'] for r in rows)} for cls in CLASSES]
    created = datetime.now(timezone.utc).isoformat()
    manifest = {'schema': 1, 'source': str(source), 'created_at': created,
                'label_scope': 'image', 'label_source': 'user_assigned_folder',
                'classes': counts, 'total_images': len(rows), 'images': rows,
                'note': 'Image classes follow the user-provided folders. Object boxes retain their existing review state.'}
    target = project/'classification'
    target.mkdir(parents=True, exist_ok=True)
    # Preserve earlier indexes if this script is intentionally rerun.
    for name in ('manifest.json', 'image_classes.csv'):
        old = target/name
        if old.exists():
            history = target/'history'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            history.mkdir(parents=True, exist_ok=True)
            (history/name).write_bytes(old.read_bytes())
    write_json(target/'manifest.json', manifest)
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    temp = target/'.image_classes.csv.tmp'
    with temp.open('w', encoding='utf-8-sig', newline='') as handle:
        handle.write(stream.getvalue())
    temp.replace(target/'image_classes.csv')
    check = list(csv.DictReader((target/'image_classes.csv').open(encoding='utf-8-sig', newline='')))
    assert len(check) == len(rows)
    assert all(r['relative_path'].split('/')[0] == r['class_name'] for r in check)
    assert all((source/r['relative_path']).is_file() for r in check)
    print(json.dumps({'path': str(target), 'total': len(rows), 'classes': counts}, ensure_ascii=False))


if __name__ == '__main__': build()
