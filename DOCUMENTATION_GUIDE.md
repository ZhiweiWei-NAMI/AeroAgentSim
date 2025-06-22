# AirFogSim Documentation Guide

This guide explains the documentation structure and helps you find the right information quickly.

## 📁 Documentation Structure Overview

```
AirFogSim/
├── README.md                    # 🚪 Project overview and quick start (English)
├── README_CN.md                 # 🚪 Project overview and quick start (Chinese)
├── INSTALL.md                   # 🔧 Detailed installation guide
├── CONTRIBUTING.md              # 🤝 Contributing guidelines
├── docs/                        # 📚 User documentation (Sphinx-based)
│   ├── README.md                # 🧭 Documentation navigation hub
│   ├── api/                     # 📖 Auto-generated API reference
│   └── guides/                  # 📋 User guides and tutorials
└── src/airfogsim/docs/          # 🔬 Technical documentation
    ├── en/                      # 🇺🇸 English technical guides
    ├── cn/                      # 🇨🇳 Chinese technical guides
    └── img/                     # 🖼️ Documentation images
```

## 🎯 Quick Navigation

### 👤 For New Users
**English**: [README.md](README.md) → [INSTALL.md](INSTALL.md) → [docs/README.md](docs/README.md)
**中文**: [README_CN.md](README_CN.md) → [INSTALL.md](INSTALL.md) → [docs/README.md](docs/README.md)

### 🔧 For Developers
**English**: [docs/README.md](docs/README.md) → [src/airfogsim/docs/en/](src/airfogsim/docs/en/)
**中文**: [docs/README.md](docs/README.md) → [src/airfogsim/docs/cn/](src/airfogsim/docs/cn/)

### 🤝 For Contributors
**Start here**: [CONTRIBUTING.md](CONTRIBUTING.md) → [docs/README.md](docs/README.md)

## 📋 Documentation Types

| Type | Location | Purpose | Audience |
|------|----------|---------|----------|
| **Project Overview** | `README.md` | Quick introduction, installation, basic usage | All users |
| **Installation Guide** | `INSTALL.md` | Detailed setup instructions | All users |
| **User Documentation** | `docs/` | API reference, tutorials, guides | End users |
| **Technical Documentation** | `src/airfogsim/docs/` | Architecture, development patterns | Developers |
| **Module Documentation** | `*/README.md` | Specific module usage | Module users |
