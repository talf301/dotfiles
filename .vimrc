set nocompatible              " be iMproved, required
filetype off                  " required

" set the runtime path to include Vundle and initialize
set rtp+=~/.vim/bundle/vundle/
call vundle#rc()
" alternatively, pass a path where Vundle should install bundles
"let path = '~/some/path/here'
"call vundle#rc(path)

" let Vundle manage Vundle, required
Bundle 'gmarik/vundle'

" The following are examples of different formats supported.
" Keep bundle commands between here and filetype plugin indent on.
" scripts on GitHub repos
" Bundle 'tpope/vim-fugitive'
" Bundle 'Lokaltog/vim-easymotion'
" Bundle 'tpope/vim-rails.git'
Bundle 'scrooloose/nerdtree'
Bundle 'scrooloose/nerdcommenter'
Bundle 'majutsushi/tagbar'
Bundle 'Valloric/YouCompleteMe'
Bundle 'kien/ctrlp.vim'
Bundle 'Lokaltog/vim-easymotion'
Bundle 'scrooloose/syntastic'
"Bundle 'jdonaldson/vaxe'
Bundle 'bling/vim-airline'
Bundle 'xolox/vim-misc'
Bundle 'lervag/vim-latex'
"Bundle 'klen/python-mode'
"Bundle 'sjl/gundo.vim'
"Bundle 'tpope/vim-fugitive'
"Bundle 'xolox/vim-session'
"Bundle 'nosami/Omnisharp'
"Bundle 'ervandew/supertab'
" The sparkup vim script is in a subdirectory of this repo called vim.
" Pass the path to set the runtimepath properly.
" Bundle 'rstacruz/sparkup', {'rtp': 'vim/'}
" scripts from http://vim-scripts.org/vim/scripts.html
" Bundle 'L9'
" Bundle 'FuzzyFinder'
" scripts not on GitHub
" git repos on your local machine (i.e. when working on your own plugin)
" Bundle 'file:///home/gmarik/path/to/plugin'
" ...

filetype plugin indent on     " required
" To ignore plugin indent changes, instead use:
"filetype plugin on
"
" Brief help
" :BundleList          - list configured bundles
" :BundleInstall(!)    - install (update) bundles
" :BundleSearch(!) foo - search (or refresh cache first) for foo
" :BundleClean(!)      - confirm (or auto-approve) removal of unused bundles
"
" see :h vundle for more details or wiki for FAQ
" NOTE: comments after Bundle commands are not allowed.
" Put your stuff after this line
set grepprg=grep\ -nH\ $*
let g:tex_flavor='latex'
syntax on
set number
colorscheme molokai
set shiftwidth=4
set tabstop=4
set hlsearch
set autowrite
nmap <F8> :TagbarToggle<CR>
nmap <F2> :NERDTreeToggle<CR>
let mapleader = ','
let g:EclimCompletionMethod = 'omnifunc'
let g:ycm_global_extra_conf = '~/.vim/bundle/YouCompleteMe/third_party/ycmd/cpp/ycm/.ycm_extra_conf'
map <Leader> <Plug>(easymotion-prefix)
set t_Co=256

nnoremap <F3> :GundoToggle<CR>

" Airline 
set laststatus=2
let g:airline_section_z = '%{strftime("%c")}'

"" Python stuff

 ""Documentation
 "let g:pymode_doc = 1
 "let g:pymode_doc_key = 'K'

 ""Linting
 "let g:pymode_lint = 1
 "let g:pymode_lint_checker = "pyflakes,pep8"
 "" Auto check on save
 "let g:pymode_lint_write = 1

 "" Support virtualenv
 "let g:pymode_virtualenv = 1

 "" Enable breakpoints plugin
 "let g:pymode_breakpoint = 1
 "let g:pymode_breakpoint_bind = '<leader>b'

 "" syntax highlighting
 "let g:pymode_syntax = 1
 "let g:pymode_syntax_all = 1
 "let g:pymode_syntax_indent_errors = g:pymode_syntax_all
 "let g:pymode_syntax_space_errors = g:pymode_syntax_all

 "" Don't autofold code
 "let g:pymode_folding = 1 
