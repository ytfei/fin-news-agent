import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { NewsFeed } from './pages/NewsFeed';
import { DeepAnalysis } from './pages/DeepAnalysis';
import { AnalysisDetail } from './pages/AnalysisDetail';
import { Reports } from './pages/Reports';
import { Chat } from './pages/Chat';
import { StockDetail } from './pages/StockDetail';
import { EvalSets } from './pages/EvalSets';
import { EvalDetail } from './pages/EvalDetail';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<NewsFeed />} />
        <Route path="/deep" element={<DeepAnalysis />} />
        <Route path="/analysis/:id" element={<AnalysisDetail />} />
        <Route path="/reports" element={<Reports />} />
        {/* 旧路由保留并重定向到报告页对应时段，避免收藏夹 / 外链失效 */}
        <Route
          path="/pre-market"
          element={<Navigate to="/reports?period=pre_market" replace />}
        />
        <Route
          path="/post-market"
          element={<Navigate to="/reports?period=post_market" replace />}
        />
        <Route path="/chat" element={<Chat />} />
        <Route path="/stocks/:tsCode" element={<StockDetail />} />
        <Route path="/eval" element={<EvalSets />} />
        <Route path="/eval/:id" element={<EvalDetail />} />
      </Route>
    </Routes>
  );
}
