#!/usr/bin/env python3
"""
Visualize a knowledge graph from exports CSV data.
Produces a publication-quality figure showing compounds, targets, and their interactions.
Generates a dynamic output filename based on the query from search_library.json.
"""
import csv
import json
import os
import re
from collections import defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import networkx as nx
import numpy as np

EXPORT_DIR = 'exports'

# ── Detect query name from search_library.json or fall back to 'graph' ──
QUERY_NAME = "graph"
search_lib_path = os.path.join(EXPORT_DIR, 'search_library.json')
try:
    with open(search_lib_path) as f:
        lib = json.load(f)
    base_query = lib.get('base_query', '').strip()
    if base_query:
        safe_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', base_query).strip('_')
        safe_name = re.sub(r'_+', '_', safe_name)[:40]
        if safe_name:
            QUERY_NAME = safe_name
    print(f"📝 Detected query: {base_query or 'unknown'} → {QUERY_NAME}")
except (FileNotFoundError, json.JSONDecodeError, KeyError):
    print(f"⚠️  Could not read {search_lib_path}, using fallback name: {QUERY_NAME}")

# ── Read nodes ──────────────────────────────────────────────
nodes = {}
with open(os.path.join(EXPORT_DIR, 'nodes.csv')) as f:
    reader = csv.DictReader(f)
    for row in reader:
        uid = row['uid:ID']
        name = row.get('name', uid)
        label = row.get(':LABEL', 'Unknown')
        smiles = row.get('smiles', '')
        formula = row.get('formula', '')
        mw = row.get('molecular_weight', '')
        bio = row.get('bioactivity_summary', '')
        nodes[uid] = {
            'name': name,
            'label': label,
            'smiles': smiles,
            'formula': formula,
            'mw': mw,
            'bio': bio,
        }

# ── Read edges ──────────────────────────────────────────────
edges = []
with open(os.path.join(EXPORT_DIR, 'edges.csv')) as f:
    reader = csv.DictReader(f)
    for row in reader:
        edges.append({
            'source': row[':START_ID'],
            'target': row[':END_ID'],
            'type': row[':TYPE'],
            'activity_type': row.get('activity_type', ''),
            'activity_value': row.get('activity_value', ''),
            'units': row.get('units', ''),
            'pchembl': row.get('pchembl_value', ''),
        })

print(f"Loaded {len(nodes)} nodes and {len(edges)} edges")

# ── Build Graph ──────────────────────────────────────────────
G = nx.MultiDiGraph()

# Add nodes with attributes
for uid, data in nodes.items():
    G.add_node(uid, **data)

# Add edges
for e in edges:
    G.add_edge(e['source'], e['target'],
               rel_type=e['type'],
               activity=f"{e['activity_type']}={e['activity_value']}{e['units']}" if e['activity_value'] else e['type'],
               pchembl=e['pchembl'])

# ── Classify node types for coloring ──────────────────────────
def classify_node(uid, data):
    """Classify nodes into categories for color coding."""
    label = data.get('label', '')
    name = data.get('name', '').lower()
    uid_upper = uid.upper()
    
    if 'COMPOUND' in uid_upper or 'LIGAND' in uid_upper:
        return 'Compound'
    if 'PROTEIN' in uid_upper:
        return 'Protein Target'
    if 'TARGET' in uid_upper:
        return 'Protein Target'
    if 'PDB' in uid_upper or 'STRUCTURE' in uid_upper:
        return 'PDB Structure'
    if 'ENZYME' in uid_upper:
        return 'Enzyme'
    if label == 'COMPOUND' or label == 'LIGAND':
        return 'Compound'
    if label == 'PROTEIN' or label == 'TARGET':
        return 'Protein Target'
    if label == 'PDB' or label == 'STRUCTURE':
        return 'PDB Structure'
    if label == 'ENZYME':
        return 'Enzyme'
    return 'Other'

# ── Node colors by type ──────────────────────────────────────
CATEGORY_COLORS = {
    'Compound':      '#2196F3',  # Blue
    'Protein Target': '#F44336',  # Red  
    'Enzyme':        '#FF9800',  # Orange
    'PDB Structure': '#4CAF50',  # Green
    'Other':         '#9E9E9E',  # Grey
}

for uid, data in nodes.items():
    data['category'] = classify_node(uid, data)
    G.nodes[uid]['category'] = data['category']
    G.nodes[uid]['viz_color'] = CATEGORY_COLORS.get(data['category'], '#9E9E9E')

# ── Edge colors by type ──────────────────────────────────────
EDGE_COLORS = {
    'INHIBITS':    '#e53935',  # Red
    'BINDS':       '#7B1FA2',  # Purple
    'HAS_STRUCTURE': '#43A047',  # Green
    'default':     '#757575',  # Grey
}

# ── Create the visualization ─────────────────────────────────
fig, ax = plt.subplots(1, 1, figsize=(24, 18))
fig.patch.set_facecolor('#FAFAFA')
ax.set_facecolor('#FAFAFA')

# Use a layout that separates categories
pos = nx.spring_layout(G, k=2.5, iterations=80, seed=42)

# Draw edges with colors by type
for u, v, data in G.edges(data=True):
    rel = data.get('rel_type', 'default')
    ecolor = EDGE_COLORS.get(rel, EDGE_COLORS['default'])
    alpha = 0.5 if rel == 'HAS_STRUCTURE' else 0.8
    lw = 1.5 if rel == 'INHIBITS' else 1.0
    nx.draw_networkx_edges(G, pos, edgelist=[(u, v)], 
                           edge_color=ecolor, alpha=alpha,
                           width=lw, arrows=True, arrowsize=12,
                           connectionstyle='arc3,rad=0.1', ax=ax)

# Draw nodes
categories = set(data['category'] for data in nodes.values())
for cat in categories:
    cat_nodes = [uid for uid, data in nodes.items() if data['category'] == cat]
    color = CATEGORY_COLORS.get(cat, '#9E9E9E')
    
    # Size by degree
    degrees = [G.degree(n) for n in cat_nodes]
    sizes = [max(300, min(2000, deg * 300)) for deg in degrees]
    
    nx.draw_networkx_nodes(G, pos, nodelist=cat_nodes,
                           node_color=color, node_size=sizes,
                           edgecolors='white', linewidths=2,
                           alpha=0.9, ax=ax)

# Draw labels for important nodes
label_nodes = {}
for uid, data in nodes.items():
    deg = G.degree(uid)
    name = data.get('name', '')
    if deg >= 2:
        # Shorten long names
        short = name[:28] + '...' if len(name) > 30 else name
        label_nodes[uid] = short

nx.draw_networkx_labels(G, pos, labels=label_nodes,
                        font_size=8, font_weight='bold',
                        font_color='#333333', ax=ax)

# ── Legend ──────────────────────────────────────────────────────
legend_elements = []
for cat, color in CATEGORY_COLORS.items():
    if cat in categories:
        legend_elements.append(mpatches.Patch(facecolor=color, edgecolor='white',
                                              label=cat))

# Edge legend
legend_elements.append(plt.Line2D([0], [0], color=EDGE_COLORS['INHIBITS'],
                                   lw=2, label='INHIBITS'))
legend_elements.append(plt.Line2D([0], [0], color=EDGE_COLORS['BINDS'],
                                   lw=2, label='BINDS'))
legend_elements.append(plt.Line2D([0], [0], color=EDGE_COLORS['HAS_STRUCTURE'],
                                   lw=2, label='HAS_STRUCTURE (PDB)'))

ax.legend(handles=legend_elements, loc='upper right', fontsize=12,
          framealpha=0.9, edgecolor='#ddd')

# ── Title & cleanup ────────────────────────────────────────────
# Count inhibitor edges
inhib_count = sum(1 for e in edges if e['type'] == 'INHIBITS')
bind_count = sum(1 for e in edges if e['type'] == 'BINDS')
struct_count = sum(1 for e in edges if e['type'] == 'HAS_STRUCTURE')

query_title = QUERY_NAME.replace('_', ' ').title()
ax.set_title(
    f'{query_title} — Knowledge Graph\n'
    f'{len(nodes)} entities · {len(edges)} relations '
    f'({inhib_count} INHIBITS · {bind_count} BINDS · {struct_count} Structures)',
    fontsize=18, fontweight='bold', pad=20, color='#222'
)
ax.axis('off')
plt.tight_layout()

# ── Save ────────────────────────────────────────────────────────
outpath = os.path.join(EXPORT_DIR, f'{QUERY_NAME}_graph.png')
plt.savefig(outpath, dpi=200, bbox_inches='tight', facecolor=fig.get_facecolor())
print(f"\n✅ Graph saved to: {outpath}")
print(f"   File size: {os.path.getsize(outpath) / 1024:.1f} KB")

# ── Also create a focused view of top inhibitors ────────────────
print(f"\n📊 Top inhibitor edges (by pChEMBL / potency):")
inhib_edges = [e for e in edges if e['type'] == 'INHIBITS' and e['activity_value']]
inhib_edges.sort(key=lambda e: float(e['pchembl']) if e.get('pchembl') and e['pchembl'] not in ('', 'None', 'null') else 0, reverse=True)

for e in inhib_edges[:15]:
    src_name = nodes.get(e['source'], {}).get('name', e['source'][:20])
    tgt_name = nodes.get(e['target'], {}).get('name', e['target'][:20])
    val = e['activity_value']
    unit = e['units']
    pchem = e['pchembl']
    act = e['activity_type']
    try:
        ic50_um = f"{float(val)/1000:.1f}"
    except ValueError:
        ic50_um = val
    print(f"   {src_name[:25]:>25} → {tgt_name[:25]:<25} "
          f"{act}={val} {unit}  " + (f"pChEMBL={pchem}" if pchem and pchem not in ('None', '') else ""))

# Clean up
plt.close()
