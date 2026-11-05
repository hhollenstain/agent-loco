# OpenAPI first

New HTTP behavior starts in `openapi/lbw.yaml`. The spec names the path, the
request, and the response. `make openapi` reads that file and writes
`src/agent_lbw/openapi_models.py`. A handler imports those models. It does
not grow a second copy of the same fields by hand.

`make openapi-check` fails when the generated file does not match the spec.
CI runs that check. The running image does not run the generator. It imports
the file that was committed with the spec.

Routes that are not in the spec yet stay as they are. When a group moves,
add its paths and schemas, regenerate, and point the handler at the generated
model in that same change. The first groups in the spec are schedules, the
audit log, and repository instruction files.
