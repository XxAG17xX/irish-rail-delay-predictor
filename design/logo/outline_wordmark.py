import sys
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
src, text, wght, wdth, track = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
f = TTFont(src)
print("axes", [(a.axisTag, a.minValue, a.maxValue) for a in f["fvar"].axes], file=sys.stderr)
feats = sorted({fr.FeatureTag for fr in f["GSUB"].table.FeatureList.FeatureRecord} | {fr.FeatureTag for fr in f["GPOS"].table.FeatureList.FeatureRecord})
print("features", feats, file=sys.stderr)
f = instantiateVariableFont(f, {"wght": wght, "wdth": wdth})
cmap, gs, hmtx, upm = f.getBestCmap(), f.getGlyphSet(), f["hmtx"], f["head"].unitsPerEm
names = [cmap[ord(c)] for c in text]
def kern(a, b):
    v = 0
    for li in f["GPOS"].table.FeatureList.FeatureRecord:
        if li.FeatureTag != "kern": continue
        for idx in li.Feature.LookupListIndex:
            lk = f["GPOS"].table.LookupList.Lookup[idx]
            for st in lk.SubTable:
                if lk.LookupType == 9: st = st.ExtSubTable
                if getattr(st, "LookupType", lk.LookupType) != 2: continue
                cov = st.Coverage.glyphs
                if a not in cov: continue
                if st.Format == 1:
                    for pvr in st.PairSet[cov.index(a)].PairValueRecord:
                        if pvr.SecondGlyph == b and pvr.Value1: return (pvr.Value1.XAdvance or 0)
                else:
                    c1 = st.ClassDef1.classDefs.get(a, 0); c2 = st.ClassDef2.classDefs.get(b, 0)
                    r = st.Class1Record[c1].Class2Record[c2].Value1
                    if r and r.XAdvance: return r.XAdvance
    return v
x, paths = 0, []
for i, n in enumerate(names):
    pen = SVGPathPen(gs)
    gs[n].draw(TransformPen(pen, (1, 0, 0, -1, x, 0)))
    paths.append(pen.getCommands())
    x += hmtx[n][0] + track * upm
    if i + 1 < len(names): x += kern(n, names[i + 1])
x -= track * upm
os2 = f["OS/2"]
print(f'{upm} {x:.1f} {os2.sCapHeight} {os2.sxHeight}')
print(" ".join(paths))
