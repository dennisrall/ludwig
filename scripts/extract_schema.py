# !/usr/bin/env python
import json
import os
import subprocess
from pathlib import Path

# Import (unused) modules with marshmallow classes, necessary for generating list of subclasses.
import ludwig.combiners.combiners as lcc  # noqa: F401
import ludwig.marshmallow.test_classes as lut  # noqa: F401
import ludwig.models.trainer as lmt  # noqa: F401
import ludwig.modules.optimization_modules as lmo  # noqa: F401
from ludwig.marshmallow.marshmallow_schema_utils import BaseMarshmallowConfig, get_fully_qualified_class_name


def all_subclasses(cls):
    """Returns recursively-generated list of all children classes inheriting from given `cls`."""
    return set(cls.__subclasses__()).union([s for c in cls.__subclasses__() for s in all_subclasses(c)])


def get_mclass_paths():
    """Returns a dict of all known marshmallow dataclasses in Ludwig paired with their fully qualified paths within
    the directory."""
    all_mclasses = list(all_subclasses(BaseMarshmallowConfig))
    all_mclasses += [BaseMarshmallowConfig, lcc.CommonTransformerConfig]
    return {cls.__name__: get_fully_qualified_class_name(cls) for cls in all_mclasses}


def get_pytkdocs_structure_for_path(path: str, docstring_style="restructured-text"):
    """Runs pytkdocs in a subprocess and returns the parsed structure of the object at the given path with the
    given documentation style."""
    pytkdocs_subprocess = subprocess.Popen(
        ["pytkdocs"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    input_json = json.dumps({"objects": [{"path": path, "docstring_style": docstring_style}]})
    pytkdocs_output = pytkdocs_subprocess.communicate(input=input_json.encode())[0]
    return json.loads(pytkdocs_output.decode())


def extract_pytorch_structures():
    """Extracts and saves the parsed structure of all pytorch classes referenced in
    `ludwig.modules.optimization_modules.optimizer_registry` as JSON files under
    `ludwig/marshmallow/generated/torch/`."""
    torch_structures = {}
    for opt in lmo.optimizer_registry:
        optimizer_class = lmo.optimizer_registry[opt][0]
        path = get_fully_qualified_class_name(optimizer_class)
        torch_structures[opt] = get_pytkdocs_structure_for_path(path, "google")
        parent_dir = str(Path(__file__).parent.parent)
        filename = os.path.join(parent_dir, "ludwig/marshmallow/generated/torch/", optimizer_class.__name__) + ".json"
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w") as outfile:
            json.dump(get_pytkdocs_structure_for_path(path, "google"), outfile)
            outfile.write("\n")


def extract_marshmallow_structures():
    """Extracts and saves the parsed structure of all known marshmallow dataclasses referenced throughout Ludwig as
    JSON files under `ludwig/marshmallow/generated/`."""
    mclass_paths = get_mclass_paths()
    mclass_structures = {}
    for cls_name, path in mclass_paths.items():
        mclass_structures[cls_name] = get_pytkdocs_structure_for_path(path)
        parent_dir = str(Path(__file__).parent.parent)
        filename = os.path.join(parent_dir, "ludwig/marshmallow/generated/", cls_name) + ".json"
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "w") as outfile:
            json.dump(get_pytkdocs_structure_for_path(path), outfile, indent=4, sort_keys=True, separators=(",", ": "))
            outfile.write("\n")


def main():
    """Simple runner for marshmallow dataclass extraction."""
    extract_pytorch_structures()
    extract_marshmallow_structures()


if __name__ == "__main__":
    main()
