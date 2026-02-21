export function parseDiffLines(content) {
  if (!content) return [];
  const lines = content.split('\n');
  const result = [];
  let oldLine = 0, newLine = 0;
  for (const line of lines) {
    const trimmed = line.trimStart();
    if (trimmed.startsWith('@@')) {
      const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)/);
      if (m) { oldLine = parseInt(m[1]); newLine = parseInt(m[2]); }
      result.push({ type:'hunk', text:line });
    } else if (/^(diff --git|index |---\s|\+\+\+\s|new file mode|deleted file mode|similarity index|rename from |rename to |old mode |new mode )/.test(trimmed)) {
      // Skip patch metadata lines such as --- /dev/null, +++ b/path
      continue;
    } else if (line.startsWith('+')) {
      result.push({ type:'add', old:'', new:newLine, text:line.slice(1) });
      newLine++;
    } else if (line.startsWith('-')) {
      result.push({ type:'del', old:oldLine, new:'', text:line.slice(1) });
      oldLine++;
    } else {
      const text = line.startsWith(' ') ? line.slice(1) : line;
      result.push({ type:'ctx', old:oldLine, new:newLine, text });
      oldLine++; newLine++;
    }
  }
  return result;
}
