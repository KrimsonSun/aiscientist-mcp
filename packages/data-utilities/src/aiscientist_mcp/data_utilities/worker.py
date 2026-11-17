"""Execute admitted utilities in the common task workspace."""

from types import SimpleNamespace
from uuid import uuid4

from aiscientist_mcp.common import jobs
from aiscientist_mcp.common.model import ModelSettings
from aiscientist_mcp.common.tasks import confined_child, worker_main

from .guards import check_dot, check_hdf5_links
from .settings import CONFIG, admitted, model_configuration


def execute(request, ws):
    operation = request["operation"]
    args = request["arguments"]
    artifacts = confined_child(ws, "artifacts")
    if operation == "inspect_dataset":
        from .inspect import inspect_dataset

        path = admitted(args["path"])
        check_hdf5_links(path)
        if request["settings"] != model_configuration():
            raise ValueError("model configuration changed after submission")
        model = ModelSettings.from_env("DATA_UTILITIES")
        chat = model.judge(max_tokens=1200) if model.endpoint_pair() else None
        result = inspect_dataset(str(path), chat_fn=chat)
        artifacts.mkdir(exist_ok=True)
        data = confined_child(artifacts, "data")
        data.mkdir(exist_ok=True)
        # A long text line or HDF5 attribute can make a bounded read a large
        # response. Preserve every inspected byte in a distinct attempt artifact.
        filename = "dataset_inspection-" + uuid4().hex + ".json"
        jobs.write_json(data, filename, result)
        return result | {"artifact_root": str(artifacts), "full_result": str(data / filename)}
    if operation != "make_schematic":
        raise ValueError("unknown operation")
    from .schematic import _exec_make_schematic

    check_dot(args["source"])
    figures = confined_child(artifacts, "figures")
    basename = str(args.get("basename", "schematic")).strip() or "schematic"
    basename = "schematic_" + "".join(c for c in basename if c.isalnum() or c in "-_")[:48]
    for suffix in (".dot", ".png"):
        target = figures / (basename + suffix)
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("diagram output must be a regular file inside the workspace")
    return _exec_make_schematic(args, SimpleNamespace(workspace=ws)) | {"artifact_root": str(artifacts)}


def main():
    worker_main(CONFIG, execute)


if __name__ == "__main__":
    main()
