import { useState } from 'react';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';

const NAV = [
  { to: '/', label: '时间线' },
  { to: '/pre-market', label: '盘前' },
  { to: '/post-market', label: '盘后' },
  { to: '/chat', label: '追问' },
  { to: '/eval', label: '评估集' },
];

export function Layout() {
  const [code, setCode] = useState('');
  const navigate = useNavigate();

  const search = () => {
    const v = code.trim();
    if (v) {
      navigate(`/stocks/${v}`);
      setCode('');
    }
  };

  return (
    <div className="app">
      <nav className="topnav">
        <span className="brand">fin-news</span>
        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) => (isActive ? 'active' : '')}
          >
            {item.label}
          </NavLink>
        ))}
        <span className="spacer" />
        <input
          type="text"
          placeholder="股票代码，如 600519.SH"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          style={{ width: 170 }}
        />
        <button className="primary" onClick={search}>
          查个股
        </button>
      </nav>
      <Outlet />
    </div>
  );
}
