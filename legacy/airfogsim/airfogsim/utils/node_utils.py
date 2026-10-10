def getNodeTypeById(node_id):
    node_id = node_id.capitalize()
    assert node_id[0] in ['V', 'R', 'U', 'C'], f'Invalid node type of {node_id}'
    if node_id[0] == 'V':
        return 'V'
    elif node_id[0] == 'R':
        return 'I'
    elif node_id[0] == 'U':
        return 'U'
    elif node_id[0] == 'C':
        return 'C'
    else:
        return None