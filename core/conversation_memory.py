import json,time
class ConversationMemory:
    def __init__(self,pool): self.pool=pool
    async def ensure(self):
        async with self.pool.acquire() as c:
            await c.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (id BIGSERIAL PRIMARY KEY,user_id TEXT NOT NULL,chat_id TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL); CREATE INDEX IF NOT EXISTS conv_idx ON conversation_messages(user_id,chat_id,created_at);""")
    async def append(self,user_id,chat_id,role,content):
        async with self.pool.acquire() as c:
            await c.execute("INSERT INTO conversation_messages(user_id,chat_id,role,content,created_at) VALUES($1,$2,$3,$4,$5)",str(user_id),str(chat_id),role,str(content),time.time())
    async def render(self,user_id,chat_id,limit=20):
        async with self.pool.acquire() as c:
            rows=await c.fetch("SELECT role,content FROM conversation_messages WHERE user_id=$1 AND chat_id=$2 ORDER BY created_at DESC LIMIT $3",str(user_id),str(chat_id),limit)
        return "\n".join("[{}] {}".format(r["role"],str(r["content"])[:1200]) for r in reversed(rows))
    async def clear(self,user_id,chat_id):
        async with self.pool.acquire() as c:
            await c.execute("DELETE FROM conversation_messages WHERE user_id=$1 AND chat_id=$2",str(user_id),str(chat_id))
