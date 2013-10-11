/* Contest Management System
 * Copyright © 2013 Luca Wehrstedt <luca.wehrstedt@gmail.com>
 * Copyright © 2013 William Di Luigi <williamdiluigi@gmail.com>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program. If not, see <http://www.gnu.org/licenses/>.
 */
'use strict';

angular.module('pws.navbar', [])
  .directive('navbar', [function() {
    return {
      restrict: 'E',
      scope: {},
      templateUrl: 'partials/navbar.html',
      replace: true,
      transclude: true,
      controller: 'NavbarCtrl',
    };
  }])
  .controller('NavbarCtrl', ['$scope', '$location', 'userManager', function($scope, $location, user) {
    $scope.getUsername = user.getUsername;
    $scope.isActivePage = function(page) {
      return $location.path().indexOf(page) == 1;
    }
  }]);
