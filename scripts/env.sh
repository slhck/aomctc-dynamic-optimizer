# Source the repository's .env file (not committed, see .env.example).
# Variables already set in the environment take precedence.
_env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.env"
if [[ -f "$_env_file" ]]; then
  while IFS='=' read -r _k _v; do
    [[ -z "$_k" || "$_k" == \#* ]] && continue
    _v="${_v%\"}"; _v="${_v#\"}"; _v="${_v%\'}"; _v="${_v#\'}"
    [[ -z "${!_k:-}" ]] && export "$_k=$_v"
  done < "$_env_file"
fi
: "${DYNOPT_ROOT:?DYNOPT_ROOT is not set; copy .env.example to .env and edit it}"
