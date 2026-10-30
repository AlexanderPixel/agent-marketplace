using Marketplace.Data;

namespace Marketplace.Compliance;

public enum SubstanceRisk { Clear, Notify, Restricted, Unknown }

public record SubstanceFinding(string Substance, string? Cas, decimal? PercentWw, string List, SubstanceRisk Risk, string Action);

public record ComponentScan(BomComponent Component, string? Declaration, SubstanceRisk Risk, List<SubstanceFinding> Findings, string? PfasFreeAlternative);

/// <summary>
/// REACH and PFAS screen of the BOM, from the suppliers' material declarations (seed data) against excerpts of the
/// SVHC Candidate List, Annex XIV (authorisation) and the proposed EU-wide PFAS restriction. Conservative (rule 7):
/// a component with no declaration is Unknown, which counts as red.
/// </summary>
public static class Substances
{
    private static readonly Dictionary<string, (string Declaration, SubstanceFinding[] Findings)> Declarations = new()
    {
        ["Ovako Hofors AB"] = ("51CrV4 spring steel, EN 10089; REACH declaration 2026-03", []),
        ["Kuźnia Jawor S.A."] = ("Forged C45/42CrMo4 parts; REACH declaration 2026-01", []),
        ["KS Gleitlager GmbH"] = ("Bronze CuSn7Pb6Zn4 bushing", [
            new("Lead", "7439-92-1", 6.0m, "SVHC Candidate List", SubstanceRisk.Notify,
                "Above 0.1% w/w: inform the buyer under REACH Art. 33 and notify SCIP. Lead-free bronze (CuSn12) is available."),
        ]),
        ["NOF Metal Coatings North America"] = ("Dacromet 320 zinc-flake coating", [
            new("Chromium trioxide (Cr VI)", "1333-82-0", 0.4m, "REACH Annex XIV (authorisation)", SubstanceRisk.Restricted,
                "Needs an authorisation for use in the EU, or a defence exemption under REACH Art. 2(3). Switch to a Cr(VI)-free coating."),
        ]),
        ["Dörken Coatings GmbH"] = ("Delta-Protekt KL 100, Cr(VI)-free", []),
        ["Daikin Industries"] = ("Polyflon PTFE interleaf pad", [
            new("Polytetrafluoroethylene (PTFE)", "9002-84-0", 100m, "Proposed EU PFAS restriction (ECHA)", SubstanceRisk.Notify,
                "PFAS: the proposed universal restriction would phase this out. Plan substitution; PFAS-free UHMW-PE pads are qualified."),
        ]),
        ["Röchling Industrial SE"] = ("Polystone M UHMW-PE, PFAS-free", []),
        ["Trelleborg Antivibration Solutions"] = ("Natural rubber/steel bushing; REACH declaration 2025-11", []),
        ["Norm Cıvata"] = ("Bolt 10.9, zinc with trivalent (Cr III) passivation", []),
        ["Drewpal sp. z o.o."] = ("ISPM 15 heat-treated pallet", []),
    };

    private static readonly Dictionary<string, string> PfasFree = new() { ["interleaf"] = "Röchling Industrial SE (UHMW-PE)" };

    public static List<ComponentScan> Scan(BillOfMaterials bom) => bom.Components.Select(c =>
    {
        if (!Declarations.TryGetValue(c.Supplier, out var d))
        {
            return new ComponentScan(c, null, SubstanceRisk.Unknown,
                [new("No material declaration", null, null, "—", SubstanceRisk.Unknown, $"Ask {c.Supplier} for a REACH/SCIP declaration before you declare compliance.")],
                null);
        }

        var risk = d.Findings.Length == 0 ? SubstanceRisk.Clear : d.Findings.Max(f => f.Risk);
        var pfas = d.Findings.Any(f => f.List.Contains("PFAS")) ? PfasFree.GetValueOrDefault(c.Id) : null;
        return new ComponentScan(c, d.Declaration, risk, [.. d.Findings], pfas);
    }).ToList();
}
