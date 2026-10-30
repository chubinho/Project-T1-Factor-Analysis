from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from .analysis import analyse, write_json, write_results
from .contract import read_contract, sha256
from .experiments import make_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce the ML OAT experiments in desktop Microsoft Excel")
    parser.add_argument("--source", type=Path, help="Supplied or DE-approved single-deal XLSX")
    parser.add_argument("--output", type=Path, required=True, help="New output directory (never overwrite a prior run)")
    parser.add_argument("--half-point", action="store_true", help="Also run 7.0%% and 8.0%%, separately from the main 24 rows")
    parser.add_argument("--capture", type=Path, help="Reanalyse a saved raw_capture.json instead of launching Excel")
    parser.add_argument("--contract", type=Path, help="source_contract.json for saved-capture reanalysis")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output already exists. Choose a new directory to preserve earlier evidence.")
    if args.capture:
        if not args.contract:
            parser.error("--capture requires --contract")
        contract = json.loads(args.contract.read_text(encoding="utf-8-sig"))
        capture = json.loads(args.capture.read_text(encoding="utf-8-sig"))
        plan = make_plan(contract, args.half_point)
    else:
        if not args.source:
            parser.error("--source is required unless --capture is supplied")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if not powershell:
            parser.error("Windows PowerShell and desktop Microsoft Excel are required for a fresh capture")
        source = args.source.resolve(strict=True)
        contract = read_contract(source)
        plan = make_plan(contract, args.half_point)
        script = Path(__file__).resolve().parents[1] / "scripts" / "capture_ml_oat.ps1"
        
        with tempfile.TemporaryDirectory(prefix="t1-ml-oat-") as temp_name:
            temp = Path(temp_name)
            source_copy = temp / "calculation.xlsx"
            shutil.copyfile(source, source_copy)
            request_path, capture_path = temp / "request.json", temp / "raw_capture.json"
            write_json(request_path, {"source_copy":str(source_copy), "contract":contract, "plan":plan})
            try:
                subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-File", str(script),
                                "-RequestPath", str(request_path), "-OutputPath", str(capture_path)], check=True, timeout=900)
                capture = json.loads(capture_path.read_text(encoding="utf-8-sig"))
            finally:
                if sha256(source) != contract["source_sha256"]:
                    raise RuntimeError("Original source changed during the run; discard the capture")
                if sha256(source_copy) != contract["source_sha256"]:
                    raise RuntimeError("Excel changed the temporary XLSX on disk; discard the capture")
    result = analyse(contract, plan, capture)
    output.mkdir(parents=True)
    write_json(output / "source_contract.json", contract)
    write_json(output / "experiment_plan.json", plan)
    write_json(output / "raw_capture.json", capture)
    write_results(output, result)
    from .plots import render_plots
    render_plots(output, result)
    print(f"Verified {len(result['journal'])} required + {len(result['optional_rate_experiments'])} optional experiments")
    print(f"DF residual: {result['df_balance']['residual']:.12g} RUB")
    print(f"Output: {output}")


if __name__ == "__main__":
    main()
