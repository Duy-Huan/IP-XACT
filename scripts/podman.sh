#!/usr/bin/env bash
# Build from committed source only; run as the caller, with separate native caches.
set -euo pipefail
repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
engine=${USP_OCI_ENGINE:-podman}
image=${USP_CONTAINER_IMAGE:-localhost/socsim-sdk:0.2.0-r1}
case "$engine" in
    podman) oci=(podman); user_args=(--userns=keep-id --user "$(id -u):$(id -g)"); label=Z ;;
    docker) oci=(docker); user_args=(--user "$(id -u):$(id -g)"); label= ;;
    *) echo 'USP_OCI_ENGINE must be podman or docker' >&2; exit 2 ;;
esac
if [[ "$engine" == docker && -n "${USP_DOCKER_HOST:-}" ]]; then
    oci+=(--host "$USP_DOCKER_HOST")
fi
command -v "$engine" >/dev/null || { echo "Install $engine on the host first" >&2; exit 2; }
command=${1:-help}
[[ $# == 0 ]] || shift
safe=(--rm --cap-drop=ALL --security-opt=no-new-privileges --pids-limit=2048)
case "$command" in
    build)
        [[ $# == 0 ]] || { echo 'Usage: podman.sh build' >&2; exit 2; }
        git -C "$repo" diff --quiet HEAD -- || { echo 'Commit tracked changes before building the release context' >&2; exit 2; }
        revision=$(git -C "$repo" rev-parse HEAD)
        context=$(mktemp -d)
        trap 'rm -rf -- "$context"' EXIT
        git -C "$repo" archive "$revision" | tar -x -C "$context"
        trust=()
        if [[ -n "${USP_BUILD_CA_FILE:-}" ]]; then
            test -r "$USP_BUILD_CA_FILE"
            trust=(--secret "id=proxy_ca,src=$USP_BUILD_CA_FILE")
        fi
        echo "Building SDK from committed source $revision"
        "${oci[@]}" build "${trust[@]}" --build-arg "SOURCE_REVISION=$revision" \
            --file "$context/Containerfile" --tag "$image" "$context"
        ;;
    smoke)
        [[ $# == 0 ]] || exit 2
        "${oci[@]}" run "${safe[@]}" "${user_args[@]}" --network=none \
            "$image" bash /opt/socsim/containers/smoke.sh
        ;;
    run)
        state=${USP_CONTAINER_STATE:-$repo/.podman}
        mkdir -p "$state"
        state=$(cd "$state" && pwd)
        label=${USP_SELINUX_LABEL-$label}
        case "$label" in Z|z|'') ;; *) echo 'SELinux label must be Z, z or empty' >&2; exit 2 ;; esac
        suffix=${label:+:$label}
        mounts=(--volume "$repo:/workspace$suffix")
        for pair in 'build:build' 'tools:.tools' 'results:results' 'venv:.venv'; do
            host=${pair%%:*}; guest=${pair#*:}
            mkdir -p "$state/$host"
            mounts+=(--volume "$state/$host:/workspace/$guest$suffix")
        done
        mkdir -p "$state/cache"
        mounts+=(--volume "$state/cache:/cache$suffix")
        tty=(); if [[ -t 0 && -t 1 ]]; then tty=(-it); fi
        [[ $# != 0 ]] || set -- bash
        "${oci[@]}" run "${safe[@]}" "${user_args[@]}" "${tty[@]}" "${mounts[@]}" \
            --env PYTHONPATH=/workspace/src --workdir /workspace "$image" "$@"
        ;;
    export)
        [[ $# == 1 ]] || { echo 'Usage: podman.sh export OUTPUT_DIRECTORY' >&2; exit 2; }
        mkdir -p "$1"; dest=$(cd "$1" && pwd)
        archive=socsim-sdk-0.2.0-r1-linux-amd64.oci.tar
        [[ ! -e "$dest/$archive" ]] || { echo 'Archive already exists; preserve it' >&2; exit 2; }
        if [[ "$engine" == podman ]]; then
            "${oci[@]}" save --format oci-archive --output "$dest/$archive" "$image"
        else
            temporary=$(mktemp -d "$dest/.docker-export.XXXXXXXX")
            trap 'rm -rf -- "$temporary"' EXIT
            "${oci[@]}" save --output "$temporary/image.tar" "$image"
            if command -v skopeo >/dev/null; then
                skopeo copy "docker-archive:$temporary/image.tar" "oci-archive:$dest/$archive:$image"
            else
                "${oci[@]}" run "${safe[@]}" "${user_args[@]}" --network=none \
                    --volume "$dest:/export" "$image" skopeo copy \
                    "docker-archive:/export/${temporary##*/}/image.tar" \
                    "oci-archive:/export/$archive:$image"
            fi
        fi
        "${oci[@]}" image inspect "$image" > "$dest/image-inspect.json"
        (cd "$dest" && sha256sum "$archive" image-inspect.json > SHA256SUMS)
        echo "$dest/$archive"
        ;;
    *)
        echo 'Usage: scripts/podman.sh {build|smoke|run [COMMAND...]|export OUTPUT_DIRECTORY}'
        [[ "$command" == help ]] || exit 2
        ;;
esac
