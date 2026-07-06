from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)

sentences = ["밈오리 테스트 문장입니다"]
output = model.encode(sentences, return_dense=True, return_sparse=True)

print(output['dense_vecs'].shape)
print(output['dense_vecs'][0][:5])