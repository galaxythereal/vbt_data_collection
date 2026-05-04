import glob
for f in glob.glob('golden_model/*.py'):
    with open(f, 'r') as file:
        content = file.read()
    content = content.replace('session_20260426_155704', 'session_20260503_180346').replace('session_20260425_001844', 'session_20260503_180346')
    with open(f, 'w') as file:
        file.write(content)
