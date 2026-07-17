import subprocess


def run_args(args, timeout=30, input_data=None):
    try:
        r = subprocess.run(
            args,
            input=input_data,
            capture_output=True,
            text=not isinstance(input_data, (bytes, bytearray)),
            timeout=timeout,
            check=False,
        )
        stdout = r.stdout.decode(errors='replace') if isinstance(r.stdout, bytes) else (r.stdout or '')
        stderr = r.stderr.decode(errors='replace') if isinstance(r.stderr, bytes) else (r.stderr or '')
        return stdout.strip(), stderr.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return '', 'Command timed out', 124
    except Exception as e:
        return '', str(e), 1


def run_shell(cmd, timeout=30):
    """Compatibility wrapper for fixed, internally-owned shell snippets only."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '').strip(), (r.stderr or '').strip(), r.returncode
    except subprocess.TimeoutExpired:
        return '', 'Command timed out', 124
    except Exception as e:
        return '', str(e), 1

