"""Expression operand shapes and outcome fields actually consumed by runtime."""
from srtp.ir_contracts import obj,array,TEXT,BOOL

EXPRESSION={'$ref':'#/$defs/expression'}
UNARY=('not','neg','abs','count','all','any')
BINARY=('eq','ne','lt','lte','gt','gte','div','mod','contains')
VARIADIC=('and','or','add','sub','mul','min','max','coalesce')


def expression_schema(compact=True):
    specs={'literal':({'value':{}},('value',)), 'ref':({'path':TEXT},('path',)),
           'param':({'name':TEXT},('name',)),'var':({'name':TEXT},('name',)),
           'call':({'function':TEXT,'args':array(EXPRESSION)},('function','args')),
           'if':({'condition':EXPRESSION,'then':EXPRESSION,'else':EXPRESSION},('condition','then','else'))}
    for op in ('list','vector'): specs[op]=({'items':array(EXPRESSION)},('items',))
    for op in UNARY+BINARY+VARIADIC:
        size=1 if op in UNARY else 2 if op in BINARY else None
        specs[op]=({'args':array(EXPRESSION,size,**({'minItems':0 if op in ('and','or','coalesce') else 1} if size is None else {}))},('args',))
    variants=[obj(dict(op={'const':op},**fields),('op',)+required) for op,(fields,required) in specs.items()]
    if compact: variants.append(obj({'expr':TEXT},('expr',)))
    return {'oneOf':variants}


def outcome_schema():
    return obj({'status':dict(TEXT,minLength=1),'terminal':BOOL,'winners':array(EXPRESSION),
        'losers':array(EXPRESSION),'scores':{'type':'object','additionalProperties':EXPRESSION}},('status','terminal'))
