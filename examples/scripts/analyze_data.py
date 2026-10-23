"""Small subprocess demo; no third-party dependencies."""
import json
import statistics
import sys

values = [float(value) for value in sys.argv[1:]] or [10, 20, 30]
print(json.dumps({'count': len(values), 'mean': statistics.mean(values)}))
print('Analysis complete', file=sys.stderr)
