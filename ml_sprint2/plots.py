from __future__ import annotations

from pathlib import Path


def render_plots(output: Path, result: dict) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":11, "axes.spines.top":False,
                         "axes.spines.right":False, "figure.facecolor":"white", "savefig.facecolor":"white"})
    journal = result["journal"]
    note = f"Одна сделка, {result['source']['valuation_date']}. Методика исходного XLSX; один стрессовый сценарий."

    def sensitivity(family: str, title: str, xlabel: str, filename: str, extra: list[dict]) -> None:
        rows = sorted([r for r in journal if r["family"] in ("control",family)]+extra,
                      key=lambda r:r["principal"] if family == "principal" else r["contract_rate"])
        x = [r["principal"]/1e6 if family == "principal" else r["contract_rate"]*100 for r in rows]
        fig, axes = plt.subplots(2, 1, figsize=(10, 7.4), sharex=True, gridspec_kw={"height_ratios":[1.3,1]}, layout="constrained")
        fig.suptitle(title, fontsize=16, x=.05, ha="left")
        for key, label, color in (("base_npv","NPV базы","#176087"),("stress_npv","NPV стресса","#d27718")):
            axes[0].plot(x, [r[key]/1000 for r in rows], marker="o", linewidth=2, label=label, color=color)
        axes[0].set_ylabel("Стоимость, тыс. RUB")
        axes[0].legend(frameon=False)
        axes[1].plot(x, [r["dnpv"]/1000 for r in rows], "o-", color="#7b3a68", linewidth=2, label="dNPV = стресс − база")
        axes[1].set_ylabel("dNPV, тыс. RUB")
        axes[1].set_xlabel(xlabel)
        for xi, row in zip(x, rows):
            axes[1].annotate(f"{row['dnpv']/1000:.2f}", (xi,row["dnpv"]/1000), xytext=(0,10), textcoords="offset points", ha="center",fontsize=10)
        for ax in axes:
            ax.grid(axis="y", alpha=.22)
            ax.yaxis.set_major_formatter(FuncFormatter(lambda val,pos:f"{val:,.0f}".replace(","," ")))
        axes[1].legend(frameon=False,loc="lower left")
        axes[1].set_xticks(x)
        fig.supxlabel(note, fontsize=9)
        fig.savefig(output/filename, dpi=180)
        plt.close(fig)

    sensitivity("principal", "Влияние остатка долга\nДоговорная ставка и DF фиксированы", "Остаток долга, млн RUB", "principal.png", [])
    rate_title = "Влияние договорной ставки\nДолг и DF фиксированы"
    if result["optional_rate_experiments"]:
        rate_title += ". Точки 7,0% и 8,0% дополнительные"
    sensitivity("contract_rate", rate_title, "Договорная ставка, % годовых", "contract_rate.png", result["optional_rate_experiments"])
    rows = [r for r in journal if r["family"] == "df"]
    values = [r["df_stress_contribution"]/1000 for r in rows]
    fig, ax = plt.subplots(figsize=(12,6.8),layout="constrained")
    ax.bar([r["bucket"] for r in rows],values,color=["#176087" if v>=0 else "#b2463e" for v in values])
    ax.axhline(0,color="#444444",linewidth=.8)
    ax.set_title("Вклады DF в исходный стрессовый dNPV",loc="left",fontsize=16,pad=16)
    ax.set_ylabel("Σ CF бакета × (DF стресса − DF базы), тыс. RUB")
    ax.set_xlabel("Бакет исходного файла")
    ax.tick_params(axis="x",rotation=45)
    ax.grid(axis="y",alpha=.22)
    balance = result["df_balance"]
    fig.supxlabel(f"Сумма: {balance['contributions_sum']:,.2f} RUB. Остаток: {balance['residual']:.2e} RUB.\n"
                  "При замене только базового DF изменение dNPV имеет противоположный знак.\n"+note, fontsize=9)
    fig.savefig(output/"df_contributions.png",dpi=180)
    plt.close(fig)
