"""Run the automatable checks from AGENT-SPEC.md section 10 against a page in each theme.

    python design/check_ui.py [URL]      # default: design/preview.html

Needs Playwright with Chromium. The page must switch theme from ?theme= (as preview.html does).
"""
import json
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

base = sys.argv[1] if len(sys.argv) > 1 else (Path(__file__).resolve().parent / "preview.html").as_uri()
chromium = os.environ.get("CHROMIUM") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
failed = False
JS='''() => {
 const out={};
 out.shouts=document.querySelectorAll('.bt-shout,.bt-app-title').length;
 const ctl=[...document.querySelectorAll('button,input,select,textarea,[role=button],[role=progressbar],.bt-ms')];
 out.tiltedControls=ctl.filter(e=>{const t=getComputedStyle(e).transform;if(t==='none')return false;const m=t.match(/matrix\\(([^)]+)\\)/);if(!m)return true;const v=m[1].split(',').map(Number);return Math.abs(v[1])>1e-3;}).length;
 out.rounded=[...document.querySelectorAll('*')].filter(e=>parseFloat(getComputedStyle(e).borderTopLeftRadius)>0).map(e=>e.className);
 out.blurShadows=[...document.querySelectorAll('*')].filter(e=>{const s=getComputedStyle(e).boxShadow;if(s==='none')return false;return s.replace(/rgba?\\([^)]*\\)/g,'').split(',').some(p=>{const n=p.replace('inset','').trim().split(/\\s+/).map(parseFloat);return (n[2]||0)>0;});}).map(e=>e.className+' '+getComputedStyle(e).boxShadow);
 out.hscroll=document.documentElement.scrollWidth>window.innerWidth;
 return out;}'''
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=chromium)
    for t in ["","blast-zone","wiretap"]:
        for w in [1280,390]:
            pg=b.new_page(viewport={"width":w,"height":900})
            pg.goto(base + (("&" if "?" in base else "?") + f"theme={t}" if t else "")); pg.wait_for_timeout(1500)
            r = pg.evaluate(JS)
            bad = r["shouts"] != 1 or r["tiltedControls"] or r["rounded"] or r["blurShadows"] or r["hscroll"]
            failed = failed or bool(bad)
            print("FAIL" if bad else "ok  ", t or "xerox-riot", w, json.dumps(r))
    b.close()
sys.exit(1 if failed else 0)
