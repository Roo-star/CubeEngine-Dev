"""Encode key moments from real captured frames; waiting time is condensed."""
import argparse
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageChops,ImageStat

OUT=Path(__file__).resolve().parent
rows=json.loads((OUT/'frames.json').read_text(encoding='utf8'))
parser=argparse.ArgumentParser();parser.add_argument('--reuse-timeline',action='store_true')
args=parser.parse_args()
events=[json.loads(line) for line in (OUT/'events.jsonl').read_text(encoding='utf8').splitlines()]
first_3d=min(e['seconds'] for e in events if e['kind']=='target' and e['data'].get('dimensions')==[3,3,3])
labels={'01 Import 2D':'01 · 載入 2D 井字棋',
        '02 Play 2D':'02 · 實際試玩 2D／勝負驗證',
        '03 Compile LLM setup':'03 · 開啟 Compile LLM／批准界面',
        '03 Compile LLM':'03 · Compile LLM — 真實模型編譯',
        '04 Approve Source':'04 · Approve — 批准來源四 IR',
        '05 Natural language':'05 · 輸入指定的自然語言轉換 Prompt',
        '06 Run Lift':'06 · Run Lift — 生成 3D 版本',
        '07 Approve Target':'07 · Approve — 批准 3D Target',
        '08 Play 3D':'08 · 新 3D Target 試玩／切層、跨層獲勝與重開'}
selected=[];last_small=None;last_key=None;last_time=-100
if args.reuse_timeline:
    by_file={row['file']:i for i,row in enumerate(rows)}
    selected=[by_file[row['file']] for row in json.loads((OUT/'gif-timeline.json').read_text(encoding='utf8'))]
for i,row in enumerate([] if args.reuse_timeline else rows):
    key=(row['phase'],row['status'])
    img=Image.open(OUT/'frames'/row['file']).convert('RGB')
    small=img.resize((288,170)).convert('L')
    changed=last_small is None or ImageStat.Stat(ImageChops.difference(small,last_small)).mean[0]>.18
    if key!=last_key or (changed and row['seconds']-last_time>=.75):
        if key!=last_key and i and (not selected or selected[-1]!=i-1):selected.append(i-1)
        selected.append(i);last_small=small;last_key=key;last_time=row['seconds']
if rows and (not selected or selected[-1]!=len(rows)-1):selected.append(len(rows)-1)
font=ImageFont.truetype('C:/Windows/Fonts/msjh.ttc',22)
images=[];durations=[];notes=[]
for n,i in enumerate(selected):
    row=rows[i];screen=Image.open(OUT/'frames'/row['file']).convert('RGB')
    frame=Image.new('RGB',(screen.width,screen.height+58),'#10151d');frame.paste(screen,(0,0))
    draw=ImageDraw.Draw(frame);draw.line((0,screen.height,screen.width,screen.height),fill='#8470b3',width=2)
    title=labels.get(row['phase'],row['phase'])
    if row['phase']=='08 Play 3D' and row['seconds']<first_3d:
        title='08 · 發現視窗仍沿用 Source／修正後重新載入新 Target'
    caption=title+'    ·    關鍵節點剪輯／等待已縮短'
    draw.text((22,screen.height+15),caption,font=font,fill='#e7e9f0')
    first=n==0 or rows[selected[n-1]]['phase']!=row['phase']
    duration=1800 if first or n==len(selected)-1 else 700
    images.append(frame.quantize(colors=256,method=Image.Quantize.MEDIANCUT))
    durations.append(duration);notes.append(dict(row,gif_duration_ms=duration))
path=OUT/'tic-tac-toe-workbench-key-moments.gif'
images[0].save(path,save_all=True,append_images=images[1:],duration=durations,loop=0,optimize=False,disposal=2)
(OUT/'gif-timeline.json').write_text(json.dumps(notes,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'file':str(path),'frames':len(images),'seconds':sum(durations)/1000,'bytes':path.stat().st_size}))
