import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

class Recommender:
    def __init__(self, csv_path):
        self.df = pd.read_csv(csv_path)
        self.df['metadata'] = (self.df['name'] + " " + self.df['category'] + " " + self.df['description']).fillna('')
        self.vectorizer = TfidfVectorizer(stop_words='english')
        self.tfidf_matrix = self.vectorizer.fit_transform(self.df['metadata'])
        self.cosine_sim = cosine_similarity(self.tfidf_matrix)

    def get_all_products(self):
        return self.df.to_dict(orient='records')

    def recommend_by_id(self, pid, n=5):
        if pid not in self.df['product_id'].values: 
            return []
        idx = self.df.index[self.df['product_id'] == pid][0]
        scores = list(enumerate(self.cosine_sim[idx]))
        scores = sorted(scores, key=lambda x: x[1], reverse=True)
        indices = [i[0] for i in scores[1:n+1]]
        return self.df.iloc[indices].to_dict(orient='records')

    def get_items_by_category(self, category, exclude_id=None, n=5):
        filtered = self.df[self.df['category'] == category]
        if exclude_id is not None:
            filtered = filtered[filtered['product_id'] != exclude_id]
        return filtered.head(n).to_dict(orient='records')

    def recommend_by_cart(self, pids, n=5):
        pids = [int(pid) for pid in pids]
        valid_ids = [pid for pid in pids if pid in self.df['product_id'].values]
        if not valid_ids: 
            return []
        idxs = [self.df.index[self.df['product_id'] == pid][0] for pid in valid_ids]
        avg_vec = np.mean(self.tfidf_matrix[idxs], axis=0)
        avg_vec = np.asarray(avg_vec)
        sims = cosine_similarity(avg_vec, self.tfidf_matrix).flatten()
        rel_idxs = sims.argsort()[::-1]
        final = []
        for i in rel_idxs:
            if self.df.iloc[i]['product_id'] not in valid_ids:
                final.append(i)
            if len(final) >= n:
                break
        return self.df.iloc[final].to_dict(orient='records')

    def recommend_by_search(self, query, n=5):
        if not query: return []
        q_vec = self.vectorizer.transform([query])
        sims = cosine_similarity(q_vec, self.tfidf_matrix).flatten()
        rel_idxs = sims.argsort()[::-1][:n]
        return self.df.iloc[rel_idxs].to_dict(orient='records')