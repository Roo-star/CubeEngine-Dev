"""Effect operands shared by Rule validation and model authoring contracts.

Names are the operands consumed by EffectTransaction._execute. Expressions and
literal identifiers are deliberately distinct; a grid is a storage scope, not
a value type. No game-specific repair or inferred gameplay lives here.
"""

# operation: (required operands, optional operands); kinds are schema references.
COMMAND_CONTRACTS = {
    'state.set': ({'target':'expression','value':'expression'}, {'scope':'expression','variable':'ruleId'}),
    'state.increment': ({'target':'expression','value':'expression'}, {'scope':'expression','variable':'ruleId'}),
    'grid.set': ({'state':'ruleId','coordinate':'expression','value':'expression'}, {}),
    'grid.toggle': ({'state':'ruleId','coordinate':'expression','off':'expression','on':'expression'}, {}),
    'entity.spawn': ({'entity_type':'ruleId'}, {'components':'expressionMap','at':'expression','as':'localId'}),
    'entity.despawn': ({'entity':'expression'}, {}),
    'entity.set': ({'entity':'expression','field':'localId','value':'expression'}, {}),
    'event.emit': ({'event':'ruleId'}, {'payload':'expression'}),
    'event.schedule': ({'event':'ruleId','delay_ticks':'expression'}, {'payload':'expression','schedule_id':'expression'}),
    'event.cancel': ({'schedule_id':'expression'}, {}),
    'phase.set': ({'phase':'expression'}, {}),
    'random.sample': ({'stream':'ruleId','domain':'expression','as':'localId'}, {}),
    'random.draw': ({'stream':'ruleId','distribution':'distribution','as':'localId'}, {}),
    'foreach': ({'query':'expression','as':'localId','effects':'commands'}, {}),
    'assert': ({'condition':'expression'}, {'message':'text'}),
}


def command_schema():
    def field_schema(kind):
        if kind == 'commands': return {'type':'array','items':{'$ref':'#/$defs/command'}}
        if kind == 'expressionMap': return {'type':'object','additionalProperties':{'$ref':'#/$defs/expression'}}
        if kind == 'text': return {'type':'string'}
        return {'$ref':'#/$defs/' + kind}
    variants=[]
    for operation,(required,optional) in COMMAND_CONTRACTS.items():
        variants.append({'type':'object','required':['op',*required],
            'properties':dict(op={'const':operation}, **{k:field_schema(v) for k,v in dict(required,**optional).items()}),
            'additionalProperties':False})
    return {'oneOf':variants}


def missing_operands(command):
    required,_ = COMMAND_CONTRACTS.get(command.get('op'), ({},{}))
    # Retain the documented pre-existing state-write shorthand for old bundles.
    return [(key,kind) for key,kind in required.items() if key not in command
            and not (key == 'target' and isinstance(command.get('variable'),str) and command['variable'].strip())]
