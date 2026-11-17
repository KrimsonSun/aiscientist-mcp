#!/bin/sh
set -eu
if [ -z "${SCGPT_INFERENCE_PYTHON:-}" ]; then
    SCGPT_INFERENCE_PYTHON=$(cat /opt/scgpt-scientific-python)
    export SCGPT_INFERENCE_PYTHON
fi
exec "$@"
