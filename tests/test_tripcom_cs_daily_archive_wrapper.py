from pathlib import Path
import os
import subprocess


WRAPPER = Path("/Users/tsimclaw/.hermes/scripts/tripcom_cs_daily_archive.sh")


def test_daily_archive_wrapper_retries_zero_results_with_default_staged_delays(tmp_path) -> None:
    text = WRAPPER.read_text(encoding="utf-8")

    assert "TRIPCOM_CS_RETRY_DELAYS_SEC" in text
    assert 'RETRY_DELAYS_SEC=(30 1800)' in text
    assert subprocess.run(["bash", "-n", str(WRAPPER)], check=False).returncode == 0

    image_archiver = tmp_path / "image_archiver.py"
    image_archiver.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sleep_args = tmp_path / "sleep-args.txt"
    imx_args = tmp_path / "imx-args.txt"
    fake_sleep = bin_dir / "sleep"
    fake_sleep.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$SLEEP_ARGS"\n', encoding="utf-8")
    fake_sleep.chmod(0o755)
    assert os.access(fake_sleep, os.X_OK)
    (tmp_path / "imx").write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$IMX_ARGS"\necho collected=0\n',
        encoding="utf-8",
    )
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        text.replace('ROOT_DIR="/Users/tsimclaw/Develop/tripcom_cs_dialog_scraping"', f'ROOT_DIR="{tmp_path}"').replace(
            'IMAGE_ARCHIVER="/Users/tsimclaw/.hermes/scripts/cs_image_archive.py"', f'IMAGE_ARCHIVER="{image_archiver}"'
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(wrapper)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SLEEP_ARGS": str(sleep_args),
            "IMX_ARGS": str(imx_args),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 3
    assert sleep_args.read_text(encoding="utf-8").splitlines() == ["30", "1800"]
    invocations = imx_args.read_text(encoding="utf-8").splitlines()
    assert len(invocations) == 3
    assert all(invocation.startswith("run collect ") for invocation in invocations)
    assert not any("export" in invocation for invocation in invocations)
    assert "collect via http" in (tmp_path / ".im_archive/logs").glob("*.log").__next__().read_text(encoding="utf-8")


def test_daily_archive_wrapper_rejects_invalid_retry_delay_override(tmp_path) -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    image_archiver = tmp_path / "image_archiver.py"
    image_archiver.write_text("", encoding="utf-8")
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        text.replace('ROOT_DIR="/Users/tsimclaw/Develop/tripcom_cs_dialog_scraping"', f'ROOT_DIR="{tmp_path}"').replace(
            'IMAGE_ARCHIVER="/Users/tsimclaw/.hermes/scripts/cs_image_archive.py"', f'IMAGE_ARCHIVER="{image_archiver}"'
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["bash", str(wrapper)],
        env={**os.environ, "TRIPCOM_CS_RETRY_DELAYS_SEC": "30 nope"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "exactly two nonnegative integer delays" in result.stdout
