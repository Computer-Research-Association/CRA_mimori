import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import AdminPage from './AdminPage.jsx'
import './index.css'

// 라우터 없이 경로 하나만 본다 — /admin은 "키워드 관리"가 새 탭으로 여는 별도
// 페이지고, 그 외 모든 경로는 지금처럼 App이 자체 화면 전환(홈/결과)으로 처리한다.
const Root = window.location.pathname === '/admin' ? AdminPage : App

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
)
