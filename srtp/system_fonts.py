"""Resolve source-declared Pygame system fonts without executing game code."""
import ast
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote


def font_request(uri):
    match=re.fullmatch(r'runtime://pygame/sysfont/([^/]+)/([01])/([01])',uri)
    if not match: return None
    family=unquote(match[1])
    if not 1<=len(family)<=128 or not re.fullmatch(r'[\w ,.-]+',family):
        raise ValueError('Unsupported system font family identifier')
    return family,bool(int(match[2])),bool(int(match[3]))


def system_font_path(uri):
    request=font_request(uri)
    if request is None: raise ValueError('Unsupported system font URI')
    # Resolve installed dependency metadata, never import a source game's module.
    from pygame import sysfont
    family,bold,italic=request
    selected=sysfont.match_font(family,bold=bold,italic=italic)
    if not selected:
        raise ValueError('Required system font unavailable locally: '+family+'; install the original font before calling the model')
    path=Path(selected).resolve(strict=True)
    exact=any(styles.get((bold,italic)) and Path(styles[(bold,italic)]).resolve()==path
              for styles in sysfont.Sysfonts.values())
    if not exact:
        raise ValueError('System font requires unsupported synthetic bold/italic: '+family)
    if not path.is_file() or path.suffix.lower() not in ('.ttf','.otf'):
        raise ValueError('System font must resolve to a readable TTF/OTF resource')
    return path


def discover_system_fonts(files):
    assets={}; missing=object()
    for relative,path in files.items():
        if path.suffix.lower()!='.py':continue
        try: tree=ast.parse(path.read_text(encoding='utf-8-sig'))
        except (SyntaxError,UnicodeError):continue
        aliases={};values={};config_refs=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):
                for item in node.names:aliases[item.asname or item.name.split('.')[0]]=item.name if item.asname else item.name.split('.')[0]
            elif isinstance(node,ast.ImportFrom) and node.module:
                for item in node.names:aliases[item.asname or item.name]=node.module+'.'+item.name
        def qualified(node):
            if isinstance(node,ast.Name):return aliases.get(node.id,node.id)
            if isinstance(node,ast.Attribute):return qualified(node.value)+'.'+node.attr
            return ''
        def value(node):
            if isinstance(node,ast.Constant):return node.value
            if isinstance(node,ast.Name):return values.get(node.id,missing)
            if isinstance(node,ast.Subscript):
                container,key=value(node.value),value(node.slice)
                if isinstance(container,dict) and isinstance(key,(str,int)):return container.get(key,missing)
            if isinstance(node,ast.Call) and qualified(node.func)=='json.load' and len(node.args)==1:
                opened=node.args[0]
                if isinstance(opened,ast.Call) and qualified(opened.func)=='open' and opened.args:
                    name=value(opened.args[0])
                    if not isinstance(name,str):return missing
                    choices=[str(PurePosixPath(relative).parent/name),name]
                    filename=next((n for n in choices if n in files and files[n].suffix.lower()=='.json'),None)
                    if filename:
                        try: result=json.loads(files[filename].read_text(encoding='utf-8-sig'))
                        except (ValueError,OSError):return missing
                        config_refs.add(filename);return result
            return missing
        # Resolve constants and literal JSON configuration only; no eval/import
        # or execution of source expressions. Ambiguous assignments stay unknown.
        assignments=[n for n in ast.walk(tree) if isinstance(n,ast.Assign)]
        ambiguous=set()
        for _ in range(2):
            for node in assignments:
                found=value(node.value)
                if found is missing:continue
                for target in node.targets:
                    if not isinstance(target,ast.Name) or target.id in ambiguous:continue
                    if target.id in values and values[target.id]!=found:
                        values.pop(target.id);ambiguous.add(target.id)
                    else:values[target.id]=found
        for node in ast.walk(tree):
            if not isinstance(node,ast.Call) or qualified(node.func)!='pygame.font.SysFont':continue
            def arg(index,key,default):
                expr=node.args[index] if len(node.args)>index else next((k.value for k in node.keywords if k.arg==key),None)
                return default if expr is None else value(expr)
            family,bold,italic=arg(0,'name',missing),arg(2,'bold',False),arg(3,'italic',False)
            if not isinstance(family,str) or type(bold) not in (bool,int) or type(italic) not in (bool,int):continue
            uri='runtime://pygame/sysfont/'+quote(family.lower(),safe='')+'/'+str(int(bool(bold)))+'/'+str(int(bool(italic)))
            if uri not in assets:
                font=system_font_path(uri);data=font.read_bytes()
                assets[uri]={'id':'asset:font.system.'+hashlib.sha256(uri.encode()).hexdigest()[:12],
                    'name':family+(' Bold' if bold else '')+(' Italic' if italic else ''),
                    'kind':'font','media_type':'font/otf' if font.suffix.lower()=='.otf' else 'font/ttf',
                    'source':{'uri':uri,'content_hash':hashlib.sha256(data).hexdigest(),'byte_size':len(data)},
                    'license':{'spdx_id':'NOASSERTION','attribution':'Installed system font: '+family,'source_uri':None,'redistribution':'unknown'},
                    'importer':{'capability':'cubeengine.raw-file','version':'1.0','settings':{}},
                    'metadata':{'requested_family':family,'bold':bool(bold),'italic':bool(italic),'resolved_file':font.name,'source_references':[]}}
            assets[uri]['metadata']['source_references'].append({'path':relative,
                'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'span':{'line_start':node.lineno,'line_end':node.end_lineno},
                'configuration':[{'path':name,'file_sha256':hashlib.sha256(files[name].read_bytes()).hexdigest()} for name in sorted(config_refs)]})
    return list(assets.values())
