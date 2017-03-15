set nocompatible              " be iMproved, required
filetype off                  " required

" set the runtime path to include Vundle and initialize
set rtp+=~/.vim/bundle/Vundle.vim
call vundle#begin()
" alternatively, pass a path where Vundle should install bundles
"let path = '~/some/path/here'
"call vundle#rc(path)

" let Vundle manage Vundle, required
Plugin 'gmarik/vundle'

" The following are examples of different formats supported.
" Keep bundle commands between here and filetype plugin indent on.
" scripts on GitHub repos
" Bundle 'tpope/vim-fugitive'
" Bundle 'Lokaltog/vim-easymotion'
" Bundle 'tpope/vim-rails.git'
Plugin 'scrooloose/nerdtree'
Plugin 'scrooloose/nerdcommenter'
Plugin 'majutsushi/tagbar'
Plugin 'Valloric/YouCompleteMe'
Plugin 'kien/ctrlp.vim'
Plugin 'Lokaltog/vim-easymotion'
Plugin 'scrooloose/syntastic'
"Bundle 'jdonaldson/vaxe'
Plugin 'bling/vim-airline'
Plugin 'xolox/vim-misc'
Plugin  'lervag/vim-latex'
Plugin 'pelodelfuego/vim-swoop'
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
call vundle#end()
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
let g:ycm_path_to_python_interpreter = '/usr/bin/python'
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
" ## added by OPAM user-setup for vim / base ## 93ee63e278bdfc07d1139a748ed3fff2 ## you can edit, but keep this line
let s:opam_share_dir = system("opam config var share")
let s:opam_share_dir = substitute(s:opam_share_dir, '[\r\n]*$', '', '')

let s:opam_configuration = {}

function! OpamConfOcpIndent()
  execute "set rtp^=" . s:opam_share_dir . "/ocp-indent/vim"
endfunction
let s:opam_configuration['ocp-indent'] = function('OpamConfOcpIndent')

function! OpamConfOcpIndex()
  execute "set rtp+=" . s:opam_share_dir . "/ocp-index/vim"
endfunction
let s:opam_configuration['ocp-index'] = function('OpamConfOcpIndex')

function! OpamConfMerlin()
  let l:dir = s:opam_share_dir . "/merlin/vim"
  execute "set rtp+=" . l:dir
endfunction
let s:opam_configuration['merlin'] = function('OpamConfMerlin')

let s:opam_packages = ["ocp-indent", "ocp-index", "merlin"]
let s:opam_check_cmdline = ["opam list --installed --short --safe --color=never"] + s:opam_packages
let s:opam_available_tools = split(system(join(s:opam_check_cmdline)))
for tool in s:opam_packages
  " Respect package order (merlin should be after ocp-index)
  if count(s:opam_available_tools, tool) > 0
    call s:opam_configuration[tool]()
  endif
endfor
" ## end of OPAM user-setup addition for vim / base ## keep this line
" ## added by OPAM user-setup for vim / ocp-indent ## c680b2d307aee8702b1552fc2f8447ee ## you can edit, but keep this line
if count(s:opam_available_tools,"ocp-indent") == 0
  source "/Users/tal/.opam/system/share/vim/syntax/ocp-indent.vim"
endif
" ## end of OPAM user-setup addition for vim / ocp-indent ## keep this line
