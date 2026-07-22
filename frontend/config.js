// Single place to point the static frontend at this project's Worker API.
// infra/provision.sh fills this in automatically for a real deployment; for local
// testing point it at `wrangler dev`'s default http://127.0.0.1:8787.
const API_BASE = "https://seng.proqual.workers.dev";
