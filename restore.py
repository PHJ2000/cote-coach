"""Restore a local lab backup. Stop the app before running this command."""
import argparse
import json
import re
import shutil
import sqlite3
import tempfile
import time
import zipfile
from pathlib import Path

def restore(archive,destination,replace=False):
    destination=destination.resolve()
    if destination.exists() and any(destination.iterdir()) and not replace:raise ValueError('대상 폴더가 비어 있지 않습니다. --replace는 기존 폴더를 백업 이름으로 옮긴 뒤 복원합니다. 먼저 서버를 종료하세요.')
    with tempfile.TemporaryDirectory(prefix='cote-restore-',dir=destination.parent) as temp:
        stage=Path(temp)/'data';stage.mkdir()
        with zipfile.ZipFile(archive) as z:
            if sum(i.file_size for i in z.infolist())>30_000_000_000:raise ValueError('30GB를 초과하는 백업입니다.')
            for item in z.infolist():
                target=(stage/item.filename).resolve()
                if not target.is_relative_to(stage) or (item.external_attr>>16)&0o170000==0o120000:raise ValueError('백업에 잘못된 경로가 있습니다.')
                if item.filename!='lab.sqlite3' and not item.filename.startswith('sessions/'):raise ValueError('알 수 없는 백업 파일입니다.')
                if item.is_dir():target.mkdir(parents=True,exist_ok=True);continue
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(item) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)
        con=sqlite3.connect(stage/'lab.sqlite3')
        try:
            if con.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('백업 DB 무결성 검사 실패')
            for sid,state in con.execute('SELECT id,state FROM sessions'):
                if not re.fullmatch('[a-f0-9]{32}',sid):raise ValueError('잘못된 세션 ID')
                s=json.loads(state)
                if s['id']!=sid:raise ValueError('세션 ID 불일치')
                for frame in s.get('frames',[]):
                    if not (stage/frame['path']).resolve().is_relative_to(stage):raise ValueError('잘못된 이미지 경로')
            for sid,kind,segment in con.execute('SELECT sid,kind,segment FROM media'):
                if not re.fullmatch('[a-f0-9]{32}',sid) or kind not in {'screen','voice'} or not re.fullmatch('[a-f0-9]{32}',segment):raise ValueError('미디어 경로 오류')
        finally:con.close()
        backup=None
        if destination.exists():
            backup=destination.with_name(destination.name+'.before-restore-'+str(time.time_ns()));destination.rename(backup)
        shutil.move(str(stage),str(destination))
        return backup

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive',type=Path);p.add_argument('--destination',type=Path,default=Path(__file__).resolve().parent/'data/lab');p.add_argument('--replace',action='store_true');args=p.parse_args()
    args.destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        backup=restore(args.archive,args.destination,args.replace)
        print('복원 완료:',args.destination)
        if backup:print('기존 기록 보관:',backup)
    except Exception as exc:p.exit(1,str(exc)+'\n')
