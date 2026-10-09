#!/usr/bin/env python3
from __future__ import annotations
import hashlib, html, json, os, re, textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from pygments import lex
from pygments.lexers import get_lexer_for_filename, TextLexer
from pygments.token import Token

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"source-code-export"; OUT.mkdir(exist_ok=True)
SKIP_DIRS={".git",".venv","venv","node_modules","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache","dist","build","source-code-export"}
SKIP_NAMES={".env",".env.local",".env.production","id_rsa","id_ed25519"}
SKIP_SUFFIXES={".png",".jpg",".jpeg",".gif",".webp",".ico",".pdf",".zip",".gz",".woff",".woff2",".ttf",".otf",".mp3",".mp4",".mov",".sqlite",".db"}
SECRET_PATH=re.compile(r"(^|/)(secrets?|credentials?)(/|\.|$)|private[_-]?key",re.I)
SECRET_VALUE=re.compile(r"(?i)(-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9]{24,}|AIza[0-9A-Za-z_-]{30,})")

def read_safe(p):
    rel=p.relative_to(ROOT).as_posix()
    if any(x in SKIP_DIRS for x in p.relative_to(ROOT).parts): return False,"generated/cache/dependency directory"
    if p.name in SKIP_NAMES or (p.name.startswith(".env.") and p.name!=".env.example"): return False,"environment/secret file"
    if SECRET_PATH.search(rel): return False,"sensitive path"
    if p.suffix.lower() in SKIP_SUFFIXES: return False,"binary/media asset"
    try:
        b=p.read_bytes()
        if b"\0" in b: return False,"binary file"
        s=b.decode("utf-8")
    except (OSError,UnicodeDecodeError): return False,"not readable UTF-8 text"
    if SECRET_VALUE.search(s): return False,"credential-like secret detected"
    return True,s

def font(size,bold=False):
    paths=["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
           "/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"]
    for p in paths:
        if os.path.exists(p): return ImageFont.truetype(p,size)
    return ImageFont.load_default()

def color(tok):
    if tok in Token.Comment: return "#8292a8"
    if tok in Token.Keyword: return "#c792ea"
    if tok in Token.Name.Function or tok in Token.Name.Class: return "#82aaff"
    if tok in Token.Name.Builtin or tok in Token.Name.Decorator: return "#ffcb6b"
    if tok in Token.Literal.String or tok in Token.Literal: return "#c3e88d"
    if tok in Token.Operator or tok in Token.Punctuation: return "#89ddff"
    if tok in Token.Number: return "#f78c6c"
    return "#dbe6ff"

def wrap_lines(source,width):
    out=[]
    for line in source.splitlines() or ([""] if source=="" else []):
        if len(line)<=width: out.append(line)
        else:
            indent=len(line)-len(line.lstrip(" \t")); cont=" "*min(indent+2,12)
            out.extend(textwrap.wrap(line,width=width,subsequent_indent=cont,replace_whitespace=False,drop_whitespace=False) or [""])
    return out

included=[]; excluded=[]
for p in sorted(ROOT.rglob("*")):
    if not p.is_file() or OUT in p.parents: continue
    ok,value=read_safe(p); rel=p.relative_to(ROOT).as_posix()
    if ok:
        raw=value.encode("utf-8")
        included.append({"path":rel,"content":value,"sha256":hashlib.sha256(raw).hexdigest(),
                         "bytes":len(raw),"lines":len(value.splitlines())})
    else: excluded.append({"path":rel,"reason":value})

# Numbered tiles are the readable, complete source. Each file remains one intact card.
TW, M, GAP, CW = 3600, 70, 50, (3600-140-50)//2
FS, LH, MAXC = 15, 20, 126
cards=[]
for f in included:
    ls=wrap_lines(f["content"],MAXC)
    cards.append({**f,"shown":ls,"height":66+len(ls)*LH+30})
groups=[]; group=[]; used=150; LIMIT=12500
for c in cards:
    if group and used+c["height"]+28>LIMIT: groups.append(group); group=[]; used=150
    group.append(c); used+=c["height"]+28
if group: groups.append(group)
tiles=[]
for no,group in enumerate(groups,1):
    pos=[]; cy=[150,150]
    for i,c in enumerate(group):
        col=i%2; x=M+col*(CW+GAP); y=cy[col]; pos.append((c,x,y)); cy[col]=y+c["height"]+28
    H=max(cy)+45; im=Image.new("RGB",(TW,H),"#0b1020"); d=ImageDraw.Draw(im)
    d.text((M,25),f"FACTCHECKBOT SOURCE CODE · TILE {no:02d}/{len(groups):02d}",font=font(30,True),fill="#f0f4ff")
    d.text((M,85),"Original repository text · visual wrapping only · hashes in manifest",font=font(17),fill="#93a4c0")
    for c,x,y in pos:
        d.rounded_rectangle((x,y,x+CW,y+c["height"]),radius=12,fill="#111a2d",outline="#31415d",width=2)
        d.rectangle((x,y,x+CW,y+54),fill="#1b2a44")
        d.text((x+24,y+14),c["path"],font=font(22,True),fill="#8ee6c8")
        d.text((x+CW-170,y+18),f'{c["lines"]} lines',font=font(15),fill="#9aacc8")
        yy=y+65
        try: lexer=get_lexer_for_filename(c["path"],stripnl=False,ensurenl=False)
        except Exception: lexer=TextLexer()
        # Pygments token colors are used line by line; each displayed row is a visual wrap.
        token_lines=[]
        current=[]
        for tok,val in lex(c["content"],lexer):
            colr=color(tok)
            for part in val.splitlines(keepends=True):
                part=part.rstrip("\r\n")
                if part: current.append((part,colr))
                if val.endswith("\n") or val.endswith("\r"):
                    token_lines.append(current); current=[]
        if current or not token_lines: token_lines.append(current)
        # Render from exact lines; color heuristic by lexer is applied to whole line to keep layout exact.
        for j,line in enumerate(c["shown"]):
            if j%2: d.rectangle((x+10,yy-1,x+CW-10,yy+LH-1),fill="#131f35")
            xx=x+24
            for tok,val in lex(line,lexer):
                shown=val.expandtabs(4)
                if shown:
                    d.text((xx,yy),shown,font=font(FS),fill=color(tok))
                    xx+=d.textlength(shown,font=font(FS))
            yy+=LH
    fn=f"repository_all_code_{no:02d}.png"; im.save(OUT/fn,optimize=True)
    tiles.append({"file":fn,"dimensions_px":[TW,H],"included_paths":[x["path"] for x in group]})

# Compact 3-column master image and scalable SVG containing every included file section.
MW, MM, MG, COLS = 5100, 55, 30, 3
MCW=(MW-2*MM-(COLS-1)*MG)//COLS; MFS, MLH, MCHARS=9,12,158
master=[]
for f in included:
    lines=wrap_lines(f["content"],MCHARS)
    master.append({**f,"shown":lines,"height":48+len(lines)*MLH+16})
rows=[master[i:i+COLS] for i in range(0,len(master),COLS)]
rowh=[max(c["height"] for c in r) for r in rows]
MH=120+sum(h+12 for h in rowh)+55
svg=['<svg xmlns="http://www.w3.org/2000/svg" width="5100" height="'+str(MH)+'" viewBox="0 0 5100 '+str(MH)+'">','<rect width="100%" height="100%" fill="#0b1020"/>',
'<style>text{font-family:DejaVu Sans Mono,monospace;white-space:pre}.title{font-size:28px;font-weight:bold;fill:#f0f4ff}.sub{font-size:13px;fill:#93a4c0}.path{font-size:15px;font-weight:bold;fill:#8ee6c8}.code{font-size:9px;fill:#dbe6ff}</style>',
'<text x="55" y="42" class="title">FACTCHECKBOT — COMPLETE REPOSITORY SOURCE GRID</text>',
f'<text x="55" y="73" class="sub">{len(included)} included UTF-8 text files · original source · see manifest for SHA-256</text>',
'<text x="55" y="96" class="sub">Wrapped for display only. Numbered PNG tiles provide readable code.</text>']
y=120
for ri,row in enumerate(rows):
    for ci,c in enumerate(row):
        x=MM+ci*(MCW+MG)
        svg.append(f'<rect x="{x}" y="{y}" width="{MCW}" height="{c["height"]}" rx="8" fill="#111a2d" stroke="#31415d"/>')
        svg.append(f'<rect x="{x}" y="{y}" width="{MCW}" height="32" rx="8" fill="#1b2a44"/>')
        svg.append(f'<text x="{x+12}" y="{y+21}" class="path">{html.escape(c["path"])}</text>')
        yy=y+45
        for line in c["shown"]:
            try: code_lexer=get_lexer_for_filename(c["path"],stripnl=False,ensurenl=False)
            except Exception: code_lexer=TextLexer()
            spans=[]
            for tok,val in lex(line,code_lexer):
                if val:
                    spans.append(f'<tspan fill="{color(tok)}">{html.escape(val.expandtabs(4))}</tspan>')
            svg.append(f'<text x="{x+12}" y="{yy}" class="code">{"".join(spans)}</text>'); yy+=MLH
    y+=rowh[ri]+12
svg.append("</svg>")
(OUT/"repository_all_code.svg").write_text("\n".join(svg),encoding="utf-8")

# Master PNG: full grid if practical; otherwise filename index plus complete code in SVG/tiles.
if MW*MH<=180_000_000:
    im=Image.new("RGB",(MW,MH),"#0b1020"); d=ImageDraw.Draw(im)
    d.text((MM,20),"FACTCHECKBOT — COMPLETE REPOSITORY SOURCE GRID",font=font(28,True),fill="#f0f4ff")
    d.text((MM,60),f"{len(included)} text files · full code grid · use tiles for readable detail",font=font(15),fill="#93a4c0")
    y=120
    for ri,row in enumerate(rows):
        for ci,c in enumerate(row):
            x=MM+ci*(MCW+MG)
            d.rounded_rectangle((x,y,x+MCW,y+c["height"]),radius=8,fill="#111a2d",outline="#31415d",width=1)
            d.rectangle((x,y,x+MCW,y+32),fill="#1b2a44")
            d.text((x+12,y+8),c["path"],font=font(15,True),fill="#8ee6c8")
            yy=y+39
            for line in c["shown"]:
                try: code_lexer=get_lexer_for_filename(c["path"],stripnl=False,ensurenl=False)
                except Exception: code_lexer=TextLexer()
                xx=x+12
                for tok,val in lex(line,code_lexer):
                    shown=val.expandtabs(4)
                    if shown:
                        d.text((xx,yy),shown,font=font(MFS),fill=color(tok))
                        xx+=d.textlength(shown,font=font(MFS))
                yy+=MLH
        y+=rowh[ri]+12
    im.save(OUT/"repository_all_code.png",optimize=True); mode="complete source grid"
else:
    im=Image.new("RGB",(2400,max(900,170+len(included)*34)),"#0b1020"); d=ImageDraw.Draw(im)
    d.text((60,30),"FACTCHECKBOT — COMPLETE SOURCE EXPORT",font=font(34,True),fill="#f0f4ff")
    d.text((60,90),f"{len(included)} files · complete code in SVG and numbered continuation PNGs",font=font(20),fill="#93a4c0")
    yy=150
    for f in included:
        d.text((70,yy),f'{f["path"]} | {f["lines"]} lines | sha256 {f["sha256"][:16]}',font=font(15),fill="#8ee6c8"); yy+=34
    im.save(OUT/"repository_all_code.png",optimize=True); mode="overview index; full source in SVG and numbered continuation tiles"

manifest={"repository":"https://github.com/arjun939201/FactCheckBot","branch":"main",
 "snapshot_commit":os.popen("git rev-parse HEAD").read().strip(),"master_png_mode":mode,
 "included_file_count":len(included),"included_files":[{k:v for k,v in f.items() if k!="content"} for f in included],
 "excluded_file_count":len(excluded),"excluded_files":excluded,"continuation_tiles":tiles,
 "complete_contents_included":True,
 "integrity_note":"SHA-256 and byte/line counts are calculated from original UTF-8 contents. Rendering wraps long lines for display but does not edit repository files. Secret-like credential values and binary files are excluded."}
(OUT/"repository_all_code_manifest.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
md=["# FactCheckBot source export manifest","",f"- Repository: {manifest['repository']}",f"- Branch: {manifest['branch']}",f"- Snapshot commit: {manifest['snapshot_commit']}",f"- Included text files: {len(included)}",f"- Excluded files: {len(excluded)}",f"- Master PNG mode: {mode}","- Complete contents included for every included file: Yes","","## Included files","","| Path | Lines | Bytes | SHA-256 |","|---|---:|---:|---|"]
md += [f"| {f['path']} | {f['lines']} | {f['bytes']} | {f['sha256']} |" for f in included]
md += ["","## Excluded files",""]+([f"- {x['path']} — {x['reason']}" for x in excluded] or ["- None"])
md += ["","## Continuation tiles",""]+[f"- {x['file']} — {len(x['included_paths'])} complete file sections; {x['dimensions_px'][0]}×{x['dimensions_px'][1]} px" for x in tiles]
(OUT/"repository_all_code_manifest.md").write_text("\n".join(md)+"\n",encoding="utf-8")
print(f"Included {len(included)} UTF-8 text files; excluded {len(excluded)} sensitive/binary/unreadable files.")
print(f"Master PNG: {mode}; SVG height {MH}px; continuation tiles {len(tiles)}.")
