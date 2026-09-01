import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Timeline } from './pages/Timeline';
import { PreMarket } from './pages/PreMarket';
import { PostMarket } from './pages/PostMarket';
import { Chat } from './pages/Chat';
import { StockDetail } from './pages/StockDetail';
import { EvalSets } from './pages/EvalSets';
import { EvalDetail } from './pages/EvalDetail';

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Timeline />} />
        <Route path="/pre-market" element={<PreMarket />} />
        <Route path="/post-market" element={<PostMarket />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/stocks/:tsCode" element={<StockDetail />} />
        <Route path="/eval" element={<EvalSets />} />
        <Route path="/eval/:id" element={<EvalDetail />} />
      </Route>
    </Routes>
  );
}
