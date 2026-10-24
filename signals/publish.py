"""Publish a directory of files to a branch as one parentless commit,
force-pushed, so the branch never accumulates history (the dashboard
republishes every couple of minutes all session)."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _git(args: list[str], cwd: Path, env: dict | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=120,
        )
    except subprocess.CalledProcessError as exc:
        # Deliberately not the command line or the raw error: the remote URL
        # carries the access token.
        raise RuntimeError(f"git {args[0]} failed (exit {exc.returncode})") from None
    return result.stdout.strip()


def publish_directory(
    directory: str, repository: str, token: str, branch: str, message: str, remote: str | None = None,
) -> None:
    """Force-push the contents of `directory` as the sole commit on `branch`
    of `repository` ("owner/name") using `token`. `remote` overrides the
    GitHub URL (for tests)."""
    remote = remote or f"https://x-access-token:{token}@github.com/{repository}.git"
    # Absolute, because git resolves GIT_WORK_TREE relative to the cwd we run in (the
    # directory itself) - a relative path like "dashboard_site" would point at
    # dashboard_site/dashboard_site.
    path = Path(directory).resolve()
    with tempfile.TemporaryDirectory() as scratch:
        # A throwaway git dir, so the working tree being published (and the
        # checkout the job runs in) is never touched.
        env = {**os.environ, "GIT_DIR": scratch, "GIT_WORK_TREE": str(path),
               "GIT_AUTHOR_NAME": "dashboard-bot", "GIT_AUTHOR_EMAIL": "dashboard-bot@users.noreply.github.com",
               "GIT_COMMITTER_NAME": "dashboard-bot", "GIT_COMMITTER_EMAIL": "dashboard-bot@users.noreply.github.com"}
        _git(["init", "-q"], path, env)
        _git(["add", "-A"], path, env)
        tree = _git(["write-tree"], path, env)
        commit = _git(["commit-tree", tree, "-m", message], path, env)
        _git(["push", "-q", "--force", remote, f"{commit}:refs/heads/{branch}"], path, env)
    logger.info("Published %s to %s", directory, branch)
