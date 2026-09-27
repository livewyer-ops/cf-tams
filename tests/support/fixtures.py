import json
from pathlib import Path


def load_json_fixture(name):
    return json.loads((Path(__file__).parents[1] / "fixtures" / name).read_text())
