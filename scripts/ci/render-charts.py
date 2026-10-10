#!/usr/bin/env python3
"""Renders every Helm chart the platform deploys, exactly as Argo CD does, and writes the manifests to stdout.

It reads the charts, versions and values from the Application files in platform/apps and from bootstrap/argocd, so CI
checks what would really be deployed and cannot drift from it. Needs helm and PyYAML. Status messages go to stderr.
"""
import glob
import os
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ARGO_CHART = ("argo-cd", "https://argoproj.github.io/argo-helm", "bootstrap/argocd/values.yaml")


def helm_template(release, namespace, chart, repo, version, values_files):
    cmd = ["helm", "template", release, chart, "--repo", repo, "--version", version, "-n", namespace, "--include-crds"]
    for f in values_files:
        cmd += ["-f", f]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.stderr.write(f"helm template failed for {release}:\n{out.stderr}\n")
        sys.exit(1)
    sys.stderr.write(f"rendered {release} {chart}@{version}\n")
    return out.stdout


def main():
    chunks, tmp = [], tempfile.mkdtemp()
    for path in sorted(glob.glob(os.path.join(ROOT, "platform/apps/*.yaml"))):
        app = yaml.safe_load(open(path))
        sources = app["spec"].get("sources") or [app["spec"]["source"]]
        chart_src = next((s for s in sources if s.get("chart")), None)
        if not chart_src:
            continue
        helm = chart_src.get("helm", {})
        files = []
        for vf in helm.get("valueFiles", []):
            files.append(os.path.join(ROOT, vf.replace("$values/", "")))
        if helm.get("valuesObject"):
            inline = os.path.join(tmp, app["metadata"]["name"] + ".yaml")
            yaml.safe_dump(helm["valuesObject"], open(inline, "w"))
            files.append(inline)
        for f in files:
            if not os.path.exists(f):
                sys.stderr.write(f"{app['metadata']['name']}: values file not found: {f}\n")
                sys.exit(1)
        chunks.append(helm_template(helm.get("releaseName", app["metadata"]["name"]), app["spec"]["destination"]["namespace"],
                                    chart_src["chart"], chart_src["repoURL"], chart_src["targetRevision"], files))
    # Argo CD itself is installed by bootstrap.sh with a pinned chart version (read from the script).
    version = next(line.split("=", 1)[1].strip() for line in open(os.path.join(ROOT, "bootstrap/bootstrap.sh"))
                   if line.startswith("ARGOCD_CHART_VERSION="))
    chunks.append(helm_template("argocd", "argocd", ARGO_CHART[0], ARGO_CHART[1], version, [os.path.join(ROOT, ARGO_CHART[2])]))
    sys.stdout.write("\n---\n".join(chunks))


if __name__ == "__main__":
    main()
