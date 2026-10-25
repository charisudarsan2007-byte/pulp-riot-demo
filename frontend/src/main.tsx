import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import '@fontsource/inter/400.css'
import '@fontsource/inter/500.css'
import '@fontsource/inter/600.css'
import '@fontsource/instrument-serif/400.css'
import '@fontsource/instrument-serif/400-italic.css'
import './index.css'
import { AppProvider } from './lib/state'
import Shell from './components/Shell'
import ControlTower from './pages/ControlTower'
import Demand from './pages/Demand'
import Stock from './pages/Stock'
import Supply from './pages/Supply'
import Money from './pages/Money'
import Decisions from './pages/Decisions'
import Replay from './pages/Replay'
import WeeklyReview from './pages/WeeklyReview'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AppProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<ControlTower />} />
            <Route path="demand" element={<Demand />} />
            <Route path="stock" element={<Stock />} />
            <Route path="supply" element={<Supply />} />
            <Route path="money" element={<Money />} />
            <Route path="decisions" element={<Decisions />} />
            <Route path="replay" element={<Replay />} />
            <Route path="weekly-review" element={<WeeklyReview />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AppProvider>
  </React.StrictMode>,
)
