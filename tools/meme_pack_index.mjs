// 重建表情包图库的 index.db 与 manifest.json（加图 / 改名 / 改描述后都要跑）
// 用法：node tools/meme_pack_index.mjs [图库目录]
// 默认图库：<记忆库>/meme-packs/shiori-learned
// 口径：tag 取 memes/<tag>/ 目录名；caption / keywords 优先沿用 index.db 里的旧值
//       （先按 path 找，路径变了按 file_name 兜底），全都没有才用文件名当描述。
import { existsSync, readdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const repo = resolve(here, '..')
const PACK = resolve(process.argv[2] || join(repo, 'meme-packs', 'shiori-learned'))
const PACK_ID = 'shiori-learned'
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.gif', '.webp'])

if (!existsSync(PACK)) throw new Error('图库目录不存在：' + PACK)
const memesDir = join(PACK, 'memes')
if (!existsSync(memesDir)) throw new Error('缺 memes 目录：' + memesDir)

const oldCaptions = new Map()
const oldKeywords = new Map()
const oldByName = new Map()
const dbPath = join(PACK, 'index.db')
if (existsSync(dbPath)) {
  try {
    const old = new DatabaseSync(dbPath, { readOnly: true })
    for (const r of old.prepare('select path, file_name, caption, keywords from memes').all()) {
      oldCaptions.set(r.path, r.caption || '')
      oldKeywords.set(r.path, r.keywords || '')
      if (r.file_name) oldByName.set(r.file_name, { caption: r.caption || '', keywords: r.keywords || '' })
    }
    old.close()
  } catch { /* 旧库坏掉就当没有，后面全量重建 */ }
}

const rows = []
for (const tag of readdirSync(memesDir)) {
  const tagDir = join(memesDir, tag)
  if (!statSync(tagDir).isDirectory()) continue
  for (const name of readdirSync(tagDir)) {
    const ext = name.slice(name.lastIndexOf('.')).toLowerCase()
    if (!IMAGE_EXTS.has(ext)) continue
    const rel = 'memes/' + tag + '/' + name
    const prev = oldByName.get(name)
    const caption = oldCaptions.get(rel) || (prev && prev.caption) || name.replace(/\.[^.]+$/, '')
    const keywords = oldKeywords.get(rel) || (prev && prev.keywords) || ''
    rows.push({ path: rel, tag, file_name: name, caption, keywords, mtime: statSync(join(tagDir, name)).mtimeMs / 1000 })
  }
}
rows.sort((a, b) => a.path.localeCompare(b.path))
if (!rows.length) throw new Error('memes 目录下没找到图片')

// 先写临时库再改名，避免写坏现有 index.db
const tmpDb = dbPath + '.new'
const db = new DatabaseSync(tmpDb)
db.exec(`CREATE TABLE memes (
  path TEXT PRIMARY KEY,
  tag TEXT NOT NULL,
  file_name TEXT NOT NULL,
  caption TEXT NOT NULL DEFAULT '',
  keywords TEXT NOT NULL DEFAULT '',
  mtime REAL,
  captioned_at REAL)`)
const ins = db.prepare('INSERT INTO memes (path, tag, file_name, caption, keywords, mtime, captioned_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
const now = Date.now() / 1000
for (const r of rows) ins.run(r.path, r.tag, r.file_name, r.caption, r.keywords, r.mtime, now)
db.close()
if (existsSync(dbPath)) renameSync(dbPath, dbPath + '.bak')
renameSync(tmpDb, dbPath)

const counts = {}
for (const r of rows) counts[r.tag] = (counts[r.tag] || 0) + 1
writeFileSync(join(PACK, 'manifest.json'), JSON.stringify({
  id: PACK_ID,
  name: 'Shiori 表情包（学习图库）',
  version: '1.0.0',
  description: '从 Downloads 的 Cache_* 整理出的表情包：文件名按画面拼音、caption 是画面中文描述，tag 归入 6 个情绪桶。总 ' + rows.length + ' 张。源清单见记忆库 references/_表情包清单.md。',
  maintainer: 'Firin',
  license: 'personal',
}, null, 2) + '\n', 'utf8')

console.log('图库：' + PACK)
console.log('重建完成：' + rows.length + ' 张  ' + JSON.stringify(counts))
console.log('旧库备份：' + dbPath + '.bak（确认没问题后可删）')
