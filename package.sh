rm -rf dist &&
  mkdir -p dist &&
  rsync -a --exclude-from=.gitignore --exclude={__pycache__,.ruff_cache,.venv,.git,*.sh,tests/,dist/,docs/,legacy/,README.md,pyrefly.toml,requirements.txt,ruff.toml,.gitignore,.gitattributes,.vscode,logo} ./ dist/ &&
  cd dist &&
  blender --command extension validate &&
  blender --command extension build &&
  find . -type f -name "*.zip" -exec mv {} ../ \; &&
  cd .. &&
  rm -rf dist
