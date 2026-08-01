# PostGIS + pgvector を 1 つのイメージにする
#
# 根拠: Docs/adr/0002-single-postgres.md（PostgreSQL 1 台に統合）
#       Docs/adr/0012-knowledge-retrieval-pgvector.md（知識検索のベクトルを同一 DB に置く）
# 手順: Docs/50_operations/database.md
#
# 公式の postgis イメージには pgvector が入っていないので、Debian パッケージを 1 つ足すだけの
# 派生イメージにする。ベースを上げるときは 16 の部分を両方（イメージタグとパッケージ名）変える。
FROM postgis/postgis:16-3.4

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-16-pgvector \
 && rm -rf /var/lib/apt/lists/*

# 拡張の作成は Alembic の最初のマイグレーションで行う（Docs/30_design/data_model.md §7.5）。
# ここでは「入れるだけ」にして、初期化 SQL をイメージに焼かない。
