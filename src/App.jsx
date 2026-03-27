import { Routes, Route } from "react-router-dom";
import Home from "./pages/Home";
import Upload from "./pages/Upload";
import Setup from "./pages/Setup";
import RehearsalRoom from "./components/RehearsalRoom";

function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/upload" element={<Upload />} />
      <Route path="/setup" element={<Setup />} />
      <Route path="/rehearse" element={<RehearsalRoom />} />
    </Routes>
  );
}

export default App;
