"""Bounded native calls used only for reading and verification."""
import os
import subprocess


class BundleError(ValueError):
    """A bundle or local system is not safe to plan."""


def run(args, *, cwd=None):
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    env.pop("APT_CONFIG", None)
    try:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True,
                                timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BundleError(f"Cannot complete {args[0]}: {type(error).__name__}") from error
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise BundleError(f"{args[0]} failed: {detail}")
    return result.stdout


def paragraphs(text):
    result, current, previous = [], {}, None
    for line in text.splitlines() + [""]:
        if not line:
            if current:
                result.append(current)
                current, previous = {}, None
        elif line[0].isspace():
            if previous is None:
                raise BundleError("Invalid Debian control continuation")
            current[previous] += "\n" + line[1:]
        else:
            key, separator, value = line.partition(":")
            if not separator or key in current:
                raise BundleError("Invalid or duplicate Debian control field")
            current[key] = value.lstrip()
            previous = key
    return result


def control_text(records):
    return "\n".join("\n".join(f"{k}: {v.replace(chr(10), chr(10) + ' ')}"
                              for k, v in item.items()) + "\n" for item in records)
